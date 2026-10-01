#!/usr/bin/env python3
"""
qualificar.py — qualificação BANT/SPIN comprimida via WhatsApp x1

Fluxo:
  start <phone>       envia 3 perguntas curtas em sequência (ou 1 só com 3 itens)
  parse               varre inbox, identifica respostas de leads em fluxo de qualificação,
                      extrai sinais (Budget / Authority / Need / Timing) e recalcula fit_score
  list                lista leads em qualificação (status=qualificacao)

Estrutura de pergunta (default — operador customiza no .env):
  Q1: "rapidão pra entender se faz sentido — qual seu maior desafio com [TEMA] hoje?"
  Q2: "você é quem decide ou tem alguém junto na decisão?"
  Q3: "qual seu prazo pra resolver isso? (essa semana / mês / trimestre / sem pressa)"

Score (fit_score 0-100):
  Need (resposta Q1)            0-40 pontos
  Authority (resposta Q2)       0-30 pontos
  Timing (resposta Q3)          0-30 pontos

Heurística de scoring:
  Q1 — qualquer resposta com >20 chars vale 25; menciona dor/problema = +15
  Q2 — "sou eu/decido eu/eu mesmo" = 30; "junto/sócio/equipe" = 20; "outra pessoa" = 5
  Q3 — "essa semana/agora/urgente" = 30; "mês/30 dias" = 20; "trimestre" = 12; "sem pressa" = 5

Após Q3, o lead é promovido pra:
  fit_score >= 70 → status = sql, tag 'fit_alto'
  fit_score 40-69 → status = mql
  fit_score < 40  → status = perdido, tag 'fit_baixo'
"""

import argparse
import os
import pathlib
import re
import sys
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
    render_first_name,
    send_with_retry,
)

load_dotenv(SCRIPT_DIR / ".env")

DEFAULT_QUESTIONS = [
    os.environ.get(
        "QUALIFY_Q1",
        "Oi {{first_name}}! Pergunta rápida pra entender se faz sentido — qual seu maior desafio hoje?"
    ),
    os.environ.get(
        "QUALIFY_Q2",
        "E nessa decisão, você é quem decide ou tem alguém junto?"
    ),
    os.environ.get(
        "QUALIFY_Q3",
        "Qual seu prazo pra resolver isso? (essa semana / esse mês / esse trimestre / sem pressa)"
    ),
]
QUALIFY_DELAY_BETWEEN_Q = int(os.environ.get("QUALIFY_DELAY_BETWEEN_Q", "60"))


def render(text, name):
    first = (name or "").split()[0] if name else ""
    return render_first_name(text, first).replace("{{name}}", name or "")


def cmd_start(args):
    init_db()
    phone = normalize_phone_e164_br(args.phone)
    if not phone:
        print("ERRO: phone inválido.", file=sys.stderr)
        return 1
    name = args.name or ""

    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"qualif_{datetime.now():%Y-%m-%d_%H%M%S}.log"
    with connect() as conn:
        upsert_lead(conn, phone, name=name)
        change_status(conn, phone, "mql", "iniciada qualificação")

    if args.combined:
        # Manda as 3 perguntas em uma mensagem só
        text = render(args.intro or "Oi {{first_name}}! 3 perguntas rápidas:", name)
        text += "\n\n"
        for i, q in enumerate(DEFAULT_QUESTIONS, 1):
            # a saudação já foi na intro: tira o "Oi {{first_name}}! " do começo da pergunta
            pergunta = render(q.replace("Oi {{first_name}}! ", ""), name)
            text += f"{i}) {pergunta[:1].upper() + pergunta[1:]}\n"
        with open(log_path, "w", encoding="utf-8") as fp:
            ok, status, err, _ = send_with_retry(phone, text, None, False, 2, 2, fp)
        with connect() as conn:
            log_touch(conn, phone, "qualificacao", text,
                      "ok" if ok else "fail", api_status=status, api_error=err,
                      campaign_id="qualif_combined")
        print(("✅" if ok else "❌") + f" 3 perguntas combinadas enviadas pra {phone}")
        return 0 if ok else 1

    # Sequencial — uma por mensagem com delay
    import time as _t
    sent = 0
    with open(log_path, "w", encoding="utf-8") as fp:
        for i, q in enumerate(DEFAULT_QUESTIONS, 1):
            rendered = render(q, name)
            ok, status, err, _ = send_with_retry(phone, rendered, None, False, 2, 2, fp)
            with connect() as conn:
                log_touch(conn, phone, "qualificacao", rendered,
                          "ok" if ok else "fail", api_status=status, api_error=err,
                          campaign_id=f"qualif_q{i}")
            print(f"  Q{i} {'OK' if ok else 'FAIL'}")
            if ok:
                sent += 1
            if i < len(DEFAULT_QUESTIONS):
                _t.sleep(QUALIFY_DELAY_BETWEEN_Q)
    print(f"✅ {sent}/3 perguntas enviadas pra {phone}")
    return 0


def score_q1(text):
    if not text:
        return 0, "vazia"
    t = text.lower()
    if len(t) < 5:
        return 5, "muito curta"
    keywords_problema = ["dificul", "problem", "desafio", "trava", "perd", "demor",
                          "compli", "ruim", "nao consigo", "nao temos", "preciso",
                          "queria", "atual"]
    has_signal = any(kw in t for kw in keywords_problema)
    base = 25 if len(t) >= 20 else 12
    return min(40, base + (15 if has_signal else 0)), ("dor mencionada" if has_signal else "resposta genérica")


def score_q2(text):
    if not text:
        return 0, "vazia"
    t = text.lower()
    if re.search(r"\b(sou eu|eu mesmo|eu decido|eu que decido|eu)\b", t) and not re.search(r"\bnao sou\b", t):
        return 30, "decisor único"
    if re.search(r"\b(junto|com (meu )?(socio|sócio|equipe|time|chefe))\b", t):
        return 20, "decisor com apoio"
    if re.search(r"\b(outra pessoa|nao sou eu|tem o|tem a)\b", t):
        return 5, "não é decisor"
    return 12, "ambíguo"


def score_q3(text):
    if not text:
        return 0, "vazia"
    t = text.lower()
    if re.search(r"\b(urgente|agora|essa semana|hoje|amanha|amanhã|imediato|asap)\b", t):
        return 30, "urgente"
    if re.search(r"\b(esse mes|este mes|30 dias|esse mês|este mês|mês que vem)\b", t):
        return 22, "este mês"
    if re.search(r"\b(trimestre|3 meses|90 dias)\b", t):
        return 12, "trimestre"
    if re.search(r"\b(sem pressa|nao tenho prazo|qualquer hora|so olhando)\b", t):
        return 3, "sem pressa"
    return 10, "ambíguo"


def cmd_parse(args):
    init_db()
    # Pega leads em qualificação que receberam pelo menos 1 toque qualificacao
    with connect() as conn:
        cur = conn.execute("""
            SELECT DISTINCT t.phone_e164, l.name, l.first_name
            FROM touches t
            JOIN leads l ON l.phone_e164 = t.phone_e164
            WHERE t.channel = 'qualificacao'
              AND t.sent_at >= ?
        """, ((datetime.now() - timedelta(days=args.window_days)).isoformat(),))
        leads = cur.fetchall()

    print(f"qualificando {len(leads)} leads em janela de {args.window_days}d...")

    promoted = 0
    for lead in leads:
        phone = lead["phone_e164"]
        with connect() as conn:
            cur = conn.execute("""
                SELECT * FROM (
                    SELECT t.sent_at AS qsent, t.text AS qtext, t.campaign_id AS qcamp
                    FROM touches t
                    WHERE t.phone_e164 = ? AND t.channel = 'qualificacao'
                    ORDER BY t.sent_at ASC
                )
            """, (phone,))
            sent_questions = cur.fetchall()

            cur = conn.execute("""
                SELECT received_at, text FROM inbox
                WHERE phone_e164 = ? AND received_at > ?
                ORDER BY received_at ASC
            """, (phone, sent_questions[0]["qsent"] if sent_questions else "1900-01-01"))
            replies = cur.fetchall()

        if not replies:
            continue

        # Pareia 1ª resposta após cada pergunta
        responses = ["", "", ""]
        for i, q in enumerate(sent_questions[:3]):
            qsent_dt = q["qsent"]
            for r in replies:
                if r["received_at"] >= qsent_dt and not responses[i]:
                    responses[i] = r["text"]
                    break

        s1, why1 = score_q1(responses[0])
        s2, why2 = score_q2(responses[1])
        s3, why3 = score_q3(responses[2])
        fit = s1 + s2 + s3

        with connect() as conn:
            conn.execute(
                "UPDATE leads SET fit_score = ?, notes = notes || ?, updated_at = ? WHERE phone_e164 = ?",
                (fit,
                 f"\n[qualif {now_iso()}] Q1={s1}({why1}) Q2={s2}({why2}) Q3={s3}({why3}) → fit={fit}",
                 now_iso(), phone),
            )
            if fit >= 70:
                change_status(conn, phone, "sql", f"fit alto {fit}")
                conn.execute(
                    "UPDATE leads SET tags = tags || ',fit_alto', updated_at = ? WHERE phone_e164 = ?",
                    (now_iso(), phone),
                )
                promoted += 1
            elif fit >= 40:
                change_status(conn, phone, "mql", f"fit médio {fit}")
            else:
                change_status(conn, phone, "perdido", f"fit baixo {fit}")
                conn.execute(
                    "UPDATE leads SET tags = tags || ',fit_baixo', updated_at = ? WHERE phone_e164 = ?",
                    (now_iso(), phone),
                )

        print(f"  {phone} {lead['name'][:25]:<25} fit={fit}  Q1={s1} Q2={s2} Q3={s3}")

    print(f"\n✅ {len(leads)} processados, {promoted} promovidos a SQL.")
    return 0


def cmd_list(args):
    init_db()
    with connect() as conn:
        cur = conn.execute("""
            SELECT phone_e164, name, status, fit_score, engagement_score,
                   last_intent, last_inbound_at, tags, touches_count
            FROM leads
            WHERE status IN ('mql', 'sql', 'em_conversa', 'proposta', 'negociacao')
            ORDER BY fit_score DESC, engagement_score DESC
            LIMIT ?
        """, (args.limit,))
        rows = cur.fetchall()
    print(f"{'PHONE':<16} {'NOME':<22} {'STATUS':<13} {'FIT':<5} {'ENG':<5} {'TOQUES':<7} {'TAGS'}")
    for r in rows:
        name = (r["name"] or "")[:20]
        print(f"{r['phone_e164']:<16} {name:<22} {r['status']:<13} "
              f"{r['fit_score']:<5} {r['engagement_score']:<5} {r['touches_count']:<7} {r['tags']}")
    print(f"\n{len(rows)} leads em qualificação/conversa.")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Qualificação BANT/SPIN via WhatsApp")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="envia 3 perguntas pra um lead")
    s.add_argument("--phone", required=True)
    s.add_argument("--name", default="")
    s.add_argument("--combined", action="store_true", help="manda as 3 numa mensagem só")
    s.add_argument("--intro", help="texto de intro antes das perguntas (modo combined)")

    p2 = sub.add_parser("parse", help="parseia respostas e calcula fit_score")
    p2.add_argument("--window-days", type=int, default=14)

    sl = sub.add_parser("list", help="lista leads em qualificação ordenados por fit")
    sl.add_argument("--limit", type=int, default=50)

    return p


def main():
    args = build_parser().parse_args()
    return {"start": cmd_start, "parse": cmd_parse, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
