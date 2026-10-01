#!/usr/bin/env python3
"""
followup.py — cadência multi-toque automática (D+0/D+1/D+3/D+7/D+14/D+30)

Conceito:
  - "Cadência" = lista ordenada de toques com offset_days e copy.
  - Cada lead em cadência tem followup_jobs gerados (1 por step).
  - Watcher checa fire_at <= now() e dispara a copy daquele step.
  - Se lead respondeu desde o último toque, PULA o resto da cadência (ou escala
    pra modo "em conversa" — operador segue manual).
  - Se lead foi pra blacklist, CANCELA tudo.

Cadências built-in (operador customiza por arquivo CSV em ./cadencias/):
  followup_padrao        (D+0, D+1, D+3, D+7, D+14)
  recuperacao_carrinho   (D+0, D+1, D+3)
  pos_proposta           (D+1, D+3, D+7, D+14, D+30 break-up)
  reativacao             (D+0, D+7, D+30)

Comandos:
  enroll      coloca um lead numa cadência (gera N followup_jobs)
  enroll-csv  coloca todo um CSV numa cadência
  watch       loop infinito que checa jobs pendentes e dispara
  fire-once   dispara TODOS os jobs com fire_at <= now() e sai (use em cron)
  list        lista jobs pendentes
  cancel      cancela cadência de um lead
  cadencias   lista cadências disponíveis
"""

import argparse
import csv
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta

from db import (
    change_status,
    connect,
    init_db,
    log_touch,
    now_iso,
    upsert_lead,
)
from disparo import (
    LOGS_DIR,
    SCRIPT_DIR,
    load_dotenv,
    normalize_phone_e164_br,
    send_with_retry,
    fill_gaps,
    render_first_name,
)

load_dotenv(SCRIPT_DIR / ".env")
CADENCIAS_DIR = SCRIPT_DIR / "cadencias"
DEFAULT_DELAY_BETWEEN_FIRES = int(os.environ.get("FOLLOWUP_DELAY", "75"))


# Cadências built-in (offset_days, copy_template)
CADENCIAS_BUILTIN = {
    "followup_padrao": [
        (0,  "Oi {{first_name}}! Tô passando aqui pra retomar nossa conversa sobre [TEMA]. Faz sentido a gente continuar?"),
        (1,  "{{first_name}}, ontem te mandei aquela mensagem sobre [TEMA]. Quer que eu mande mais detalhes ou prefere a gente conversar rapidinho?"),
        (3,  "{{first_name}}, lembrei de você. Acho que pode ser útil: [BENEFÍCIO ESPECÍFICO]. Quer ver?"),
        (7,  "Oi {{first_name}}, faz uma semana que não falamos. Mudou alguma coisa do seu lado? Posso te ajudar agora?"),
        (14, "{{first_name}}, vou parar de te incomodar. Se um dia fizer sentido [TEMA], chama aqui. Abraço."),
    ],
    "recuperacao_carrinho": [
        (0,  "Oi {{first_name}}! Vi que você começou a comprar [PRODUTO] e não finalizou. Algum problema com o pagamento? Posso ajudar?"),
        (1,  "{{first_name}}, ainda dá pra fechar com o desconto de ontem. Quer que eu te mande o link da última etapa?"),
        (3,  "{{first_name}}, hoje é o último dia do desconto. Depois disso volta ao preço cheio. Vai querer aproveitar?"),
    ],
    "pos_proposta": [
        (1,  "Oi {{first_name}}! Conseguiu olhar a proposta que mandei ontem? Qualquer dúvida tô aqui."),
        (3,  "{{first_name}}, passou pelo time? Se faltou algum dado, me avisa que eu adapto."),
        (7,  "Oi {{first_name}}, faz uma semana da proposta. Vamos fechar essa semana ou prefere reagendar a conversa?"),
        (14, "{{first_name}}, vou marcar a proposta como em standby do meu lado. Quando voltar a fazer sentido, me chama."),
        (30, "Oi {{first_name}}! Mês passado a gente conversou sobre [TEMA]. Mudou alguma coisa? Tô abrindo agenda pra retomar quem ficou."),
    ],
    "reativacao": [
        (0,  "Oi {{first_name}}! Faz tempo que a gente não conversa. Tô só passando pra avisar que [NOVIDADE]. Se ainda faz sentido, dá uma olhada 👇\n[LINK]"),
        (7,  "{{first_name}}, semana passada te mandei aquela novidade. Faz sentido pra você ou prefere que eu pare de mandar?"),
        (30, "Oi {{first_name}}! Última mensagem que mando. Se um dia precisar, sabe onde achar. Abraço."),
    ],
}


def load_cadencia(name):
    """Tenta arquivo CSV em ./cadencias/<name>.csv (offset_days,text), depois built-in."""
    csv_path = CADENCIAS_DIR / f"{name}.csv"
    if csv_path.is_file():
        steps = []
        with open(csv_path, "r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                offset = int(row.get("offset_days", "0"))
                text = row.get("text", "").replace("\\n", "\n")
                steps.append((offset, text))
        return steps
    return CADENCIAS_BUILTIN.get(name)


def cmd_cadencias(args):
    print("Cadências disponíveis:")
    print("\nBuilt-in:")
    for name, steps in CADENCIAS_BUILTIN.items():
        print(f"  {name}  ({len(steps)} toques)  → D+{', D+'.join(str(o) for o, _ in steps)}")
    print("\nCustom (./cadencias/*.csv):")
    if CADENCIAS_DIR.is_dir():
        for csv_file in CADENCIAS_DIR.glob("*.csv"):
            steps = load_cadencia(csv_file.stem)
            print(f"  {csv_file.stem}  ({len(steps)} toques)")
    else:
        print("  (pasta cadencias/ ainda não existe)")
    return 0


def _enroll_one(conn, phone, name, cadencia_name, start_at=None):
    steps = load_cadencia(cadencia_name)
    if not steps:
        raise ValueError(f"Cadência não encontrada: {cadencia_name}")

    upsert_lead(conn, phone, name=name, source=f"cadencia:{cadencia_name}")

    cur = conn.execute(
        "SELECT 1 FROM followup_jobs WHERE phone_e164 = ? AND cadence_name = ? AND status IN ('pending','done')",
        (phone, cadencia_name),
    )
    if cur.fetchone():
        return 0  # já enrolled

    base = start_at or datetime.now()
    inserted = 0
    for step_idx, (offset_days, _text) in enumerate(steps):
        fire_at = (base + timedelta(days=offset_days)).replace(microsecond=0).isoformat()
        conn.execute(
            "INSERT INTO followup_jobs (phone_e164, cadence_name, step, fire_at, status) VALUES (?, ?, ?, ?, 'pending')",
            (phone, cadencia_name, step_idx, fire_at),
        )
        inserted += 1
    return inserted


def _warn_gaps(cadence_name):
    """Avisa se os textos da cadência têm lacuna ([TEMA], [PRODUTO]...) ainda sem preencher no .env."""
    missing = []
    for _, template in (load_cadencia(cadence_name) or []):
        for var in fill_gaps(template)[1]:
            if var not in missing:
                missing.append(var)
    if missing:
        print(f"⚠️ Os textos de '{cadence_name}' usam {', '.join(missing)}. "
              f"Preencha no .env — enquanto estiver vazio, essas mensagens não são enviadas.")


def cmd_enroll(args):
    init_db()
    phone = normalize_phone_e164_br(args.phone)
    if not phone:
        print("ERRO: phone inválido.", file=sys.stderr)
        return 1
    with connect() as conn:
        n = _enroll_one(conn, phone, args.name or "", args.cadencia)
    if n == 0:
        print(f"⚠️ {phone} já está enrolled em '{args.cadencia}'.")
        return 0
    print(f"✅ {phone} enrolled em '{args.cadencia}' — {n} jobs criados.")
    _warn_gaps(args.cadencia)
    return 0


def cmd_enroll_csv(args):
    init_db()
    csv_path = pathlib.Path(args.input).expanduser()
    if not csv_path.is_file():
        print(f"ERRO: CSV não encontrado: {csv_path}", file=sys.stderr)
        return 1
    enrolled = 0
    skipped = 0
    invalid = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        with connect() as conn:
            for row in reader:
                raw_phone = row.get("phone") or row.get("Phone") or row.get("phone_e164") or ""
                phone = normalize_phone_e164_br(raw_phone)
                if not phone:
                    invalid += 1
                    continue
                name = row.get("name") or row.get("Name") or ""
                n = _enroll_one(conn, phone, name, args.cadencia)
                if n:
                    enrolled += 1
                else:
                    skipped += 1
    print(f"✅ {enrolled} enrolled | {skipped} já estavam | {invalid} inválidos")
    return 0


def _should_skip(conn, phone, cadencia_name):
    """Pula se: lead foi pra blacklist OU respondeu desde o início da cadência."""
    cur = conn.execute("SELECT blacklisted, status, last_inbound_at FROM leads WHERE phone_e164 = ?", (phone,))
    row = cur.fetchone()
    if not row:
        return None
    if row["blacklisted"]:
        return "blacklist"
    if row["status"] in ("perdido", "ganho"):
        return f"status_{row['status']}"

    if row["last_inbound_at"]:
        cur = conn.execute(
            "SELECT MIN(fire_at) AS first_fire FROM followup_jobs WHERE phone_e164 = ? AND cadence_name = ?",
            (phone, cadencia_name),
        )
        first_fire = cur.fetchone()["first_fire"]
        if first_fire and row["last_inbound_at"] >= first_fire:
            return "respondeu"

    return None


def cmd_fire_once(args):
    init_db()
    now = datetime.now().isoformat()
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"followup_{datetime.now():%Y-%m-%d_%H%M%S}.log"

    with connect() as conn:
        cur = conn.execute(
            "SELECT id, phone_e164, cadence_name, step FROM followup_jobs "
            "WHERE status = 'pending' AND fire_at <= ? ORDER BY fire_at ASC LIMIT ?",
            (now, args.limit),
        )
        jobs = cur.fetchall()

    print(f"fire-once: {len(jobs)} jobs prontos pra disparar")
    fired = skipped = failed = blocked = 0

    with open(log_path, "w", encoding="utf-8") as log_fp:
        log_fp.write(f"# followup fire-once {now_iso()} jobs={len(jobs)}\n")
        for job in jobs:
            phone = job["phone_e164"]

            with connect() as conn:
                skip_reason = _should_skip(conn, phone, job["cadence_name"])
            if skip_reason:
                with connect() as conn:
                    conn.execute(
                        "UPDATE followup_jobs SET status = 'skipped', skip_reason = ?, fired_at = ? WHERE id = ?",
                        (skip_reason, now_iso(), job["id"]),
                    )
                print(f"  ↪ skip {phone} ({skip_reason})")
                skipped += 1
                continue

            steps = load_cadencia(job["cadence_name"])
            if not steps or job["step"] >= len(steps):
                with connect() as conn:
                    conn.execute("UPDATE followup_jobs SET status = 'cancelled' WHERE id = ?", (job["id"],))
                continue

            offset_days, copy_template = steps[job["step"]]
            with connect() as conn:
                cur = conn.execute("SELECT name, first_name FROM leads WHERE phone_e164 = ?", (phone,))
                lead = cur.fetchone()
            first = lead["first_name"] if lead else ""
            text = render_first_name(copy_template, first).replace("{{name}}", (lead["name"] if lead else "") or "")
            text, missing = fill_gaps(text)
            if missing:
                # texto pronto com lacuna sem preencher: NÃO envia; o job continua pendente
                print(f"  ⏸ {phone} {job['cadence_name']} step{job['step']}: falta preencher "
                      f"{', '.join(missing)} no .env — mensagem NÃO enviada")
                blocked += 1
                continue

            ok, status, err, _ = send_with_retry(phone, text, None, False, 2, 2, log_fp)
            with connect() as conn:
                log_touch(
                    conn, phone, "followup", text,
                    "ok" if ok else "fail", api_status=status, api_error=err,
                    campaign_id=f"{job['cadence_name']}_step{job['step']}",
                )
                conn.execute(
                    "UPDATE followup_jobs SET status = ?, fired_at = ? WHERE id = ?",
                    ("done" if ok else "pending", now_iso(), job["id"]),
                )
            if ok:
                print(f"  ✅ {phone} {job['cadence_name']} step{job['step']} (D+{offset_days})")
                fired += 1
            else:
                print(f"  ❌ {phone} step{job['step']} falhou: {err}")
                failed += 1

            time.sleep(DEFAULT_DELAY_BETWEEN_FIRES)

    print(f"\n✅ {fired} disparados | {skipped} skipped | {failed} falhas"
          + (f" | {blocked} aguardando preencher o .env" if blocked else ""))
    return 0


def cmd_watch(args):
    print(f"watch followup a cada {args.interval}s. Ctrl+C pra parar.")
    while True:
        try:
            cmd_fire_once(args)
        except Exception as exc:
            print(f"[watch] erro: {exc}", file=sys.stderr)
        time.sleep(args.interval)


def cmd_list(args):
    init_db()
    with connect() as conn:
        cur = conn.execute("""
            SELECT j.phone_e164, l.name, j.cadence_name, j.step, j.fire_at, j.status
            FROM followup_jobs j
            LEFT JOIN leads l ON l.phone_e164 = j.phone_e164
            WHERE j.status = ?
            ORDER BY j.fire_at ASC
            LIMIT ?
        """, (args.status, args.limit))
        rows = cur.fetchall()
    print(f"{'FIRE_AT':<20} {'PHONE':<16} {'NOME':<22} {'CADENCE':<22} {'STEP':<5}")
    for r in rows:
        print(f"{r['fire_at'][:19]:<20} {r['phone_e164']:<16} "
              f"{(r['name'] or '')[:20]:<22} {r['cadence_name']:<22} {r['step']:<5}")
    print(f"\n{len(rows)} jobs com status={args.status}")
    return 0


def cmd_cancel(args):
    init_db()
    phone = normalize_phone_e164_br(args.phone)
    if not phone:
        print("ERRO: phone inválido.", file=sys.stderr)
        return 1
    with connect() as conn:
        cur = conn.execute(
            "UPDATE followup_jobs SET status = 'cancelled', skip_reason = ? WHERE phone_e164 = ? AND status = 'pending'"
            + (" AND cadence_name = ?" if args.cadencia else ""),
            (args.reason or "manual", phone) + ((args.cadencia,) if args.cadencia else ()),
        )
        n = cur.rowcount
    print(f"✅ {n} jobs cancelados pra {phone}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Cadência de followup multi-toque")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("cadencias", help="lista cadências disponíveis")

    se = sub.add_parser("enroll")
    se.add_argument("--phone", required=True)
    se.add_argument("--name", default="")
    se.add_argument("--cadencia", required=True)

    sec = sub.add_parser("enroll-csv")
    sec.add_argument("--input", required=True)
    sec.add_argument("--cadencia", required=True)

    sf = sub.add_parser("fire-once", help="dispara jobs com fire_at <= now()")
    sf.add_argument("--limit", type=int, default=200)

    sw = sub.add_parser("watch", help="loop infinito de fire-once")
    sw.add_argument("--interval", type=int, default=300)
    sw.add_argument("--limit", type=int, default=200)

    sl = sub.add_parser("list")
    sl.add_argument("--status", default="pending", choices=["pending", "done", "skipped", "cancelled"])
    sl.add_argument("--limit", type=int, default=100)

    sc = sub.add_parser("cancel")
    sc.add_argument("--phone", required=True)
    sc.add_argument("--cadencia")
    sc.add_argument("--reason")

    return p


def main():
    args = build_parser().parse_args()
    return {
        "cadencias":   cmd_cadencias,
        "enroll":      cmd_enroll,
        "enroll-csv":  cmd_enroll_csv,
        "fire-once":   cmd_fire_once,
        "watch":       cmd_watch,
        "list":        cmd_list,
        "cancel":      cmd_cancel,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
