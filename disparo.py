#!/usr/bin/env python3
"""
disparo.py — core do agente whatsapp-zappfy-grupos

Modos:
  listar         lista grupos da instância (com role do operador) e exporta CSV
  preview        mostra copy + alvos + ETA, NÃO envia
  teste          envia para TEST_NUMBER (sem mention)
  broadcast      dispara nos GRUPOS do CSV (exige --confirmed-test, mention=true)
  x1             dispara 1:1 pra cada CONTATO do CSV (exige --confirmed-test)
                 - personalização por {{name}} / {{first_name}} / {{phone}}
                 - sem mentionEveryone
  retry          re-tenta apenas as falhas registradas em logs/falhas_*.log
                 (detecta automaticamente se é grupo ou x1)
  agendar        agenda um disparo (broadcast OU x1) pra data/hora futura
  agenda-list    lista jobs agendados
  agenda-cancel  cancela job agendado por id

Recursos premium:
  - Jitter no delay (--jitter 0.2 = ±20%) pra fingir humano
  - Retry com backoff exponencial (--retry 3 --backoff 2)
  - Blacklist persistente (--blacklist ./blacklist.txt)
  - Personalização x1 por placeholders sem dependência externa
  - Logs estruturados com timestamp ISO + motivo de falha
  - Falhas isoladas em arquivo separado pra retry pontual
  - Health-check automático antes de envio > 50 destinos

Credenciais:
  Lê .env na pasta do script. NUNCA hardcode token.
  Vars: ZAPPFY_TOKEN (obrig), TEST_NUMBER (obrig pro teste), API_BASE (opc).
"""

import argparse
import base64
import csv
import json
import mimetypes
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / "grupos.csv"
DEFAULT_CONTATOS_CSV = SCRIPT_DIR / "contatos.csv"
DEFAULT_BLACKLIST = SCRIPT_DIR / "blacklist.txt"
SCHEDULE_DIR = SCRIPT_DIR / "scheduled"
LOGS_DIR = SCRIPT_DIR / "logs"
DEFAULT_DELAY = 60
MIN_DELAY_GROUP = 30
MIN_DELAY_X1 = 45            # x1 é mais sensível pra ban — delay mínimo maior
DEFAULT_DELAY_X1 = 75
DEFAULT_JITTER = 0.2
DEFAULT_RETRY = 3
DEFAULT_BACKOFF = 2
HEALTH_CHECK_THRESHOLD = 50


def load_dotenv(env_path):
    if not env_path.is_file():
        return
    with open(env_path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(SCRIPT_DIR / ".env")

API_BASE = os.environ.get("API_BASE", "https://api.zappfy.io").rstrip("/")
TOKEN = os.environ.get("ZAPPFY_TOKEN", "").strip()
TEST_NUMBER = os.environ.get("TEST_NUMBER", "").strip()
OPERATOR_NUMBER = os.environ.get("OPERATOR_NUMBER", "").strip() or TEST_NUMBER


def require_token():
    if not TOKEN:
        print(
            "ERRO: ZAPPFY_TOKEN ausente. Crie .env com:\n"
            "  ZAPPFY_TOKEN=<uuid>\n  TEST_NUMBER=<5511...>\n"
            "Ainda não tem a API do WhatsApp? Contrate em https://zappfy.io e copie a\n"
            "API Key da instância (passo a passo: COMO-INSTALAR.md, seção 1.1).\n",
            file=sys.stderr,
        )
        sys.exit(2)


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def api_request(method, endpoint, data=None, timeout=30):
    require_token()
    url = f"{API_BASE}{endpoint}?token={TOKEN}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return {"ok": True, "status": resp.status, "body": json.loads(raw) if raw else {}}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8") if exc.fp else ""
        return {"ok": False, "status": exc.code, "error": err_body[:500]}
    except urllib.error.URLError as exc:
        return {"ok": False, "status": 0, "error": f"URLError: {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "status": -1, "error": f"{type(exc).__name__}: {exc}"}


def normalize_phone_e164_br(raw):
    """Normaliza número BR pra E.164 (55+DDD+numero, só dígitos). None se inválido."""
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if not digits:
        return None
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) in (10, 11):
        return "55" + digits
    if len(digits) >= 10:
        return digits
    return None


def load_contatos(csv_path):
    """Carrega CSV 1:1 com colunas obrigatória `phone` e opcionais `name`/qualquer outra.
    Retorna lista de dicts com `phone_e164` normalizado + todas as colunas originais."""
    csv_path = pathlib.Path(csv_path).expanduser()
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"CSV de contatos não encontrado: {csv_path}\n"
            f"Crie um CSV com cabeçalho `phone,name` (uma linha por contato)."
        )
    contatos = []
    invalidos = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if not reader.fieldnames or "phone" not in [f.lower() for f in reader.fieldnames]:
            raise ValueError(
                "CSV de contatos precisa ter coluna 'phone' (E.164 BR ou DDD+número)."
            )
        phone_key = next(f for f in reader.fieldnames if f.lower() == "phone")
        for row in reader:
            phone_e164 = normalize_phone_e164_br(row.get(phone_key, ""))
            if not phone_e164:
                invalidos += 1
                continue
            entry = {k: (v or "").strip() for k, v in row.items()}
            entry["phone_e164"] = phone_e164
            entry["name"] = entry.get("name") or entry.get("Name") or entry.get("nome") or ""
            contatos.append(entry)
    if invalidos:
        print(f"⚠️  {invalidos} linhas com phone inválido foram descartadas.", file=sys.stderr)
    return contatos


def render_template(text, contato):
    """Substitui {{name}} {{first_name}} {{phone}} + colunas do CSV + custom_fields do DB.
    Mantém placeholder não-resolvido como string vazia."""
    name = contato.get("name", "") or ""
    first_name = name.split()[0] if name else ""
    phone = contato.get("phone_e164", "")
    base = {
        "name": name,
        "first_name": first_name,
        "primeiro_nome": first_name,
        "phone": phone,
    }
    base.update({k: v for k, v in contato.items() if isinstance(v, str)})

    # Tenta enriquecer com custom_fields do DB (se db.py existir e lead estiver lá)
    try:
        import json as _json
        from db import connect as _connect
        with _connect() as _conn:
            _cur = _conn.execute("SELECT custom_fields FROM leads WHERE phone_e164 = ?", (phone,))
            _row = _cur.fetchone()
            if _row and _row["custom_fields"]:
                try:
                    _custom = _json.loads(_row["custom_fields"])
                    for _k, _v in _custom.items():
                        base.setdefault(_k, str(_v) if _v is not None else "")
                except Exception:
                    pass
    except Exception:
        pass  # DB ainda não inicializado — segue só com colunas do CSV

    def _sub(match):
        key = match.group(1).strip().lower()
        for k, v in base.items():
            if k.lower() == key:
                return str(v)
        return ""

    import re as _re
    return _re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", _sub, text)


def load_groups(csv_path):
    csv_path = pathlib.Path(csv_path).expanduser()
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"CSV não encontrado: {csv_path}\n"
            f"Use `python3 disparo.py listar --csv-out grupos.csv` pra gerar."
        )
    groups = []
    with open(csv_path, "r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            if "name" not in row or "jid" not in row:
                raise ValueError("CSV precisa ter colunas: name,jid")
            groups.append({"name": row["name"].strip(), "jid": row["jid"].strip()})
    return groups


def load_blacklist(path):
    path = pathlib.Path(path).expanduser()
    if not path.is_file():
        return set()
    numbers = set()
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            digits = "".join(c for c in line if c.isdigit())
            if digits:
                numbers.add(digits)
    return numbers


def load_text(args):
    if args.text is not None:
        return args.text
    if args.text_file is not None:
        with open(pathlib.Path(args.text_file).expanduser(), "r", encoding="utf-8") as fp:
            return fp.read()
    raise ValueError("Use --text ou --text-file.")


def detect_media_type(mime_type):
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    raise ValueError(f"Mídia não suportada: {mime_type}")


def encode_media(media_value):
    if media_value is None:
        return None
    if media_value.startswith("data:"):
        header = media_value.split(",", 1)[0]
        mime_type = header[5:].split(";", 1)[0]
        return {"data_url": media_value, "type": detect_media_type(mime_type)}
    media_path = pathlib.Path(media_value).expanduser()
    if not media_path.is_file():
        raise FileNotFoundError(f"Mídia não encontrada: {media_path}")
    mime_type, _ = mimetypes.guess_type(str(media_path))
    if mime_type is None:
        mime_type = "application/octet-stream"
    media_type = detect_media_type(mime_type)
    encoded = base64.b64encode(media_path.read_bytes()).decode("ascii")
    return {"data_url": f"data:{mime_type};base64,{encoded}", "type": media_type}


def build_payload(number, text, media_data=None, mention_everyone=False):
    if media_data:
        return {
            "number": number,
            "type": media_data["type"],
            "file": media_data["data_url"],
            "text": text,
            "mentionEveryone": mention_everyone,
        }
    return {"number": number, "text": text, "mentionEveryone": mention_everyone}


def send_with_retry(number, text, media_data, mention_everyone, retry, backoff, log_fp):
    """Envia com retry exponencial. Retorna (ok, status, error, attempts)."""
    payload = build_payload(number, text, media_data, mention_everyone)
    endpoint = "/send/media" if media_data else "/send/text"
    last = None
    for attempt in range(1, retry + 2):
        result = api_request("POST", endpoint, payload)
        last = result
        if result["ok"]:
            log_fp.write(f"{now_iso()}|{number}|OK|attempt={attempt}|status={result['status']}\n")
            return True, result["status"], None, attempt
        log_fp.write(
            f"{now_iso()}|{number}|RETRY|attempt={attempt}|status={result['status']}|err={result.get('error','')[:200]}\n"
        )
        if attempt <= retry:
            sleep_for = backoff ** attempt
            time.sleep(sleep_for)
    log_fp.write(f"{now_iso()}|{number}|FAIL|status={last['status']}|err={last.get('error','')[:200]}\n")
    return False, last["status"], last.get("error", ""), retry + 1


def fetch_groups_from_api():
    response = api_request("GET", "/group/list")
    if not response["ok"]:
        raise RuntimeError(f"Falha /group/list: status={response['status']} err={response.get('error','')}")
    body = response["body"]
    if isinstance(body, dict):
        return body.get("groups", [])
    return body


def is_operator_admin(group, operator_number):
    if not operator_number:
        return None
    op_digits = "".join(c for c in operator_number if c.isdigit())
    for p in group.get("Participants", []):
        phone = "".join(c for c in p.get("PhoneNumber", "") if c.isdigit())
        if phone == op_digits:
            return bool(p.get("IsAdmin") or p.get("IsSuperAdmin"))
    return None


def cmd_listar(args):
    api_groups = fetch_groups_from_api()
    print("=" * 90)
    print(f"GRUPOS NA INSTÂNCIA ({len(api_groups)})")
    if OPERATOR_NUMBER:
        print(f"Operador: {OPERATOR_NUMBER} (role detectada por participação)")
    print("=" * 90)
    rows = []
    n_admin = 0
    n_member = 0
    for index, group in enumerate(api_groups, start=1):
        name = group.get("Name", "(sem nome)")
        jid = group.get("JID", "")
        n = len(group.get("Participants", []))
        is_admin = is_operator_admin(group, OPERATOR_NUMBER)
        role = "admin" if is_admin else ("membro" if is_admin is False else "?")
        if is_admin:
            n_admin += 1
        elif is_admin is False:
            n_member += 1
        rows.append({"name": name, "jid": jid, "participants": n, "role": role})
        print(f"{index:>3}. {name[:45]:<45} | {jid:<35} | {n:>4} | {role}")

    print("=" * 90)
    print(f"Total: {len(api_groups)} | admin: {n_admin} | membro: {n_member}")

    if args.csv_out:
        out = pathlib.Path(args.csv_out).expanduser()
        with open(out, "w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=["name", "jid"])
            writer.writeheader()
            for r in rows:
                writer.writerow({"name": r["name"], "jid": r["jid"]})
        print(f"CSV: {out}")
    return 0


def print_message_preview(text):
    print("MENSAGEM:")
    print("---")
    print(text, end="" if text.endswith("\n") else "\n")
    print("---")


def cmd_preview(args, text, media_data, groups):
    blacklist = load_blacklist(args.blacklist) if args.blacklist else set()
    eta_min = (len(groups) * args.delay * (1 + args.jitter / 2)) / 60
    print("=" * 70)
    print("PREVIEW — NADA SERÁ ENVIADO")
    print("=" * 70)
    print(f"Test number:           {TEST_NUMBER or '(vazio)'}")
    print(f"Grupos no CSV:         {len(groups)}")
    print(f"Mídia:                 {'sim ('+media_data['type']+')' if media_data else 'não'}")
    print(f"Delay base:            {args.delay}s | jitter ±{int(args.jitter*100)}%")
    print(f"Retry:                 {args.retry}x com backoff {args.backoff}^n")
    print(f"Blacklist:             {len(blacklist)} números")
    print(f"ETA aproximado:        ~{eta_min:.1f} min")
    print("=" * 70)
    print_message_preview(text)
    print("\nGRUPOS ALVO:")
    for i, g in enumerate(groups, 1):
        print(f"  {i:>3}. {g['name']} ({g['jid']})")
    print("\nNada foi enviado. Próximo: `python3 disparo.py teste --text-file ...`")
    return 0


def cmd_teste(text, media_data):
    if not TEST_NUMBER:
        print("ERRO: TEST_NUMBER ausente no .env.", file=sys.stderr)
        return 1
    print("=" * 70)
    print(f"TESTE → {TEST_NUMBER}")
    print("=" * 70)
    print_message_preview(text)
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"teste_{datetime.now():%Y-%m-%d_%H%M%S}.log"
    with open(log_path, "w", encoding="utf-8") as fp:
        fp.write(f"# teste {now_iso()}\n")
        ok, status, err, attempts = send_with_retry(
            TEST_NUMBER, text, media_data, False, 1, 1, fp
        )
    if ok:
        print(f"Teste OK (status {status}). Confira no WhatsApp ANTES do broadcast.")
        return 0
    print(f"Falha no teste: status {status} err={err}", file=sys.stderr)
    return 1


def maybe_health_check(n_groups):
    if n_groups < HEALTH_CHECK_THRESHOLD:
        return True
    print(f"Health-check pré-broadcast (>{HEALTH_CHECK_THRESHOLD} grupos)...")
    r = api_request("GET", "/group/list", timeout=10)
    if not r["ok"]:
        print(f"❌ Health-check falhou: status={r['status']}. Abortando.", file=sys.stderr)
        return False
    print(f"🟢 Instância OK (status {r['status']}).")
    return True


def cmd_broadcast(args, text, media_data, groups):
    if not args.confirmed_test:
        print("Bloqueado: rode `teste` primeiro e use --confirmed-test.", file=sys.stderr)
        return 1
    if args.delay < MIN_DELAY_GROUP:
        print(f"ERRO: delay mínimo {MIN_DELAY_GROUP}s. Recebido: {args.delay}s.", file=sys.stderr)
        return 1
    if not maybe_health_check(len(groups)):
        return 1

    blacklist = load_blacklist(args.blacklist) if args.blacklist else set()
    if args.max > 0:
        groups = groups[: args.max]

    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = pathlib.Path(args.log) if args.log else LOGS_DIR / f"disparo_{ts}.log"
    fail_path = LOGS_DIR / f"falhas_{ts}.log"

    print("=" * 70)
    print(f"BROADCAST — {len(groups)} grupos | delay {args.delay}s ±{int(args.jitter*100)}% | retry {args.retry}")
    print(f"Log: {log_path}")
    print("=" * 70)
    print_message_preview(text)
    print()

    success = fail = retry_ok = 0
    started = time.time()

    with open(log_path, "w", encoding="utf-8") as log_fp, open(fail_path, "w", encoding="utf-8") as fail_fp:
        log_fp.write(f"# broadcast {now_iso()} groups={len(groups)} delay={args.delay} jitter={args.jitter} retry={args.retry}\n")
        log_fp.write(f"# blacklist_size={len(blacklist)}\n")

        for index, group in enumerate(groups, 1):
            print(f"[{index:>3}/{len(groups)}] {group['name'][:40]:<40}", end=" ", flush=True)
            ok, status, err, attempts = send_with_retry(
                group["jid"], text, media_data, True, args.retry, args.backoff, log_fp
            )
            if ok:
                if attempts > 1:
                    print(f"OK (retry {attempts})", flush=True)
                    retry_ok += 1
                else:
                    print("OK", flush=True)
                success += 1
            else:
                print(f"FALHOU ({status})", flush=True)
                fail += 1
                fail_fp.write(f"{group['jid']}|{group['name']}|{status}|{(err or '')[:200]}\n")

            if index < len(groups):
                jitter = 1 + random.uniform(-args.jitter, args.jitter)
                time.sleep(args.delay * jitter)

    elapsed_min = (time.time() - started) / 60
    print()
    print("=" * 70)
    print(f"RESULTADO: {success} OK | {fail} falhas | {retry_ok} via retry | {elapsed_min:.1f} min")
    print(f"Log: {log_path}")
    if fail:
        print(f"Falhas: {fail_path}  (rode `disparo.py retry --log {fail_path}` pra re-tentar)")
    print("=" * 70)
    return 0 if fail == 0 else 1


def cmd_retry(args):
    fail_log = pathlib.Path(args.log).expanduser()
    if not fail_log.is_file():
        print(f"Log de falhas não encontrado: {fail_log}", file=sys.stderr)
        return 1
    text = load_text(args)
    media_data = encode_media(args.image)
    groups = []
    with open(fail_log, "r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("|")
            if len(parts) >= 2:
                groups.append({"jid": parts[0], "name": parts[1]})
    if not groups:
        print("Log de falhas vazio.")
        return 0
    print(f"Retry de {len(groups)} grupos...")
    args.confirmed_test = True
    return cmd_broadcast(args, text, media_data, groups)


def cmd_agendar(args, text, media_data):
    when = datetime.fromisoformat(args.when)
    if when <= datetime.now():
        print("ERRO: --when no passado.", file=sys.stderr)
        return 1
    SCHEDULE_DIR.mkdir(exist_ok=True)
    job_id = str(uuid.uuid4())[:8]
    job_mode = "x1" if args.x1 else "broadcast"
    job = {
        "id": job_id,
        "mode": job_mode,
        "when": when.isoformat(),
        "csv": str(pathlib.Path(args.csv).resolve()),
        "contatos": str(pathlib.Path(args.contatos).resolve()) if args.contatos else None,
        "text_file": str(pathlib.Path(args.text_file).resolve()) if args.text_file else None,
        "text": args.text,
        "image": args.image,
        "delay": args.delay,
        "jitter": args.jitter,
        "retry": args.retry,
        "backoff": args.backoff,
        "blacklist": args.blacklist,
        "created_at": now_iso(),
    }
    job_path = SCHEDULE_DIR / f"{job_id}.json"
    with open(job_path, "w", encoding="utf-8") as fp:
        json.dump(job, fp, indent=2)
    print(f"✅ agendado #{job_id} pra {when:%Y-%m-%d %H:%M}")
    print(f"Job: {job_path}")
    print(f"Pra executar quando chegar a hora, rode em background: `python3 disparo.py agenda-watch &`")
    return 0


def cmd_agenda_list():
    SCHEDULE_DIR.mkdir(exist_ok=True)
    jobs = sorted(SCHEDULE_DIR.glob("*.json"))
    if not jobs:
        print("Sem jobs agendados.")
        return 0
    print(f"{'ID':<10} {'QUANDO':<20} {'GRUPOS':<8} CSV")
    for jp in jobs:
        with open(jp, "r", encoding="utf-8") as fp:
            j = json.load(fp)
        try:
            n = len(load_groups(j["csv"]))
        except Exception:
            n = "?"
        print(f"{j['id']:<10} {j['when'][:19]:<20} {str(n):<8} {j['csv']}")
    return 0


def cmd_agenda_cancel(args):
    job_path = SCHEDULE_DIR / f"{args.job_id}.json"
    if not job_path.is_file():
        print(f"Job não encontrado: {args.job_id}", file=sys.stderr)
        return 1
    job_path.unlink()
    print(f"✅ job {args.job_id} cancelado.")
    return 0


def cmd_agenda_watch(args):
    """Loop que checa scheduled/ e executa quando chegar a hora."""
    SCHEDULE_DIR.mkdir(exist_ok=True)
    print(f"[{now_iso()}] watcher iniciado. Checa a cada 30s. Ctrl+C pra parar.")
    while True:
        for jp in sorted(SCHEDULE_DIR.glob("*.json")):
            try:
                with open(jp, "r", encoding="utf-8") as fp:
                    j = json.load(fp)
                when = datetime.fromisoformat(j["when"])
                if datetime.now() >= when:
                    print(f"[{now_iso()}] executando job {j['id']} (mode={j.get('mode','broadcast')})...")
                    text = j["text"] if j["text"] else open(j["text_file"], "r", encoding="utf-8").read()
                    media_data = encode_media(j["image"])

                    class A: pass
                    a = A()
                    a.confirmed_test = True
                    a.delay = j["delay"]
                    a.jitter = j["jitter"]
                    a.retry = j["retry"]
                    a.backoff = j["backoff"]
                    a.blacklist = j["blacklist"]
                    a.max = 0
                    a.log = None

                    if j.get("mode") == "x1":
                        contatos_path = j.get("contatos") or j["csv"]
                        contatos = load_contatos(contatos_path)
                        cmd_x1(a, text, media_data, contatos)
                    else:
                        groups = load_groups(j["csv"])
                        cmd_broadcast(a, text, media_data, groups)
                    jp.unlink()
                    print(f"[{now_iso()}] job {j['id']} concluído e removido.")
            except Exception as exc:
                print(f"[{now_iso()}] erro processando {jp}: {exc}", file=sys.stderr)
        time.sleep(30)


def cmd_x1(args, text_template, media_data, contatos):
    """Disparo 1:1 (x1) — envia pra cada contato individualmente, com personalização."""
    if not args.confirmed_test:
        print("Bloqueado: rode `teste` primeiro e use --confirmed-test.", file=sys.stderr)
        return 1
    if args.delay < MIN_DELAY_X1:
        print(f"ERRO: delay mínimo x1 é {MIN_DELAY_X1}s. Recebido: {args.delay}s.", file=sys.stderr)
        return 1
    if not maybe_health_check(len(contatos)):
        return 1

    blacklist = load_blacklist(args.blacklist) if args.blacklist else set()
    contatos_filtrados = []
    blocked = 0
    for c in contatos:
        digits = "".join(ch for ch in c["phone_e164"] if ch.isdigit())
        if digits in blacklist:
            blocked += 1
            continue
        contatos_filtrados.append(c)

    if args.max > 0:
        contatos_filtrados = contatos_filtrados[: args.max]

    if not contatos_filtrados:
        print("ERRO: 0 contatos após blacklist/limite. Nada a fazer.", file=sys.stderr)
        return 1

    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = pathlib.Path(args.log) if args.log else LOGS_DIR / f"x1_{ts}.log"
    fail_path = LOGS_DIR / f"falhas_x1_{ts}.log"

    print("=" * 70)
    print(f"X1 — {len(contatos_filtrados)} contatos | delay {args.delay}s ±{int(args.jitter*100)}% | retry {args.retry}")
    print(f"Blacklist removeu: {blocked}")
    print(f"Log: {log_path}")
    print("=" * 70)
    print("PREVIEW DA COPY (com placeholders):")
    print("---")
    print(text_template, end="" if text_template.endswith("\n") else "\n")
    print("---")
    if contatos_filtrados:
        print(f"\nExemplo renderizado pro primeiro contato ({contatos_filtrados[0].get('name','sem nome')}):")
        print("---")
        print(render_template(text_template, contatos_filtrados[0]))
        print("---")
    print()

    success = fail = retry_ok = 0
    started = time.time()

    with open(log_path, "w", encoding="utf-8") as log_fp, open(fail_path, "w", encoding="utf-8") as fail_fp:
        log_fp.write(
            f"# x1 {now_iso()} contatos={len(contatos_filtrados)} delay={args.delay} "
            f"jitter={args.jitter} retry={args.retry} blacklist_size={len(blacklist)}\n"
        )

        # Inicializa DB e cria/atualiza leads — best-effort, não bloqueia disparo
        try:
            from db import connect as _db_connect, init_db as _db_init, log_touch as _db_log_touch, upsert_lead as _db_upsert
            _db_init()
            with _db_connect() as _conn:
                for c in contatos_filtrados:
                    _db_upsert(_conn, c["phone_e164"], name=c.get("name", ""), source="x1_csv")
            _db_ok = True
        except Exception as _e:
            log_fp.write(f"# WARN db init falhou: {_e}\n")
            _db_ok = False

        campaign_id = f"x1_{ts}"

        for index, contato in enumerate(contatos_filtrados, 1):
            phone = contato["phone_e164"]
            name = contato.get("name") or "(sem nome)"
            personalized = render_template(text_template, contato)
            print(f"[{index:>3}/{len(contatos_filtrados)}] {name[:30]:<30} {phone[:15]:<15}", end=" ", flush=True)
            ok, status, err, attempts = send_with_retry(
                phone, personalized, media_data, False, args.retry, args.backoff, log_fp
            )
            if _db_ok:
                try:
                    with _db_connect() as _conn:
                        _db_log_touch(
                            _conn, phone, "x1", personalized,
                            "ok" if ok else "fail",
                            api_status=status, api_error=err,
                            campaign_id=campaign_id,
                            media_type=media_data["type"] if media_data else None,
                        )
                except Exception:
                    pass
            if ok:
                if attempts > 1:
                    print(f"OK (retry {attempts})", flush=True)
                    retry_ok += 1
                else:
                    print("OK", flush=True)
                success += 1
            else:
                print(f"FALHOU ({status})", flush=True)
                fail += 1
                fail_fp.write(f"{phone}|{name}|{status}|{(err or '')[:200]}\n")

            if index < len(contatos_filtrados):
                jitter = 1 + random.uniform(-args.jitter, args.jitter)
                time.sleep(args.delay * jitter)

    elapsed_min = (time.time() - started) / 60
    print()
    print("=" * 70)
    print(f"RESULTADO X1: {success} OK | {fail} falhas | {retry_ok} via retry | {elapsed_min:.1f} min")
    print(f"Log: {log_path}")
    if fail:
        print(f"Falhas: {fail_path}  (rode `disparo.py retry --log {fail_path} --x1` pra re-tentar)")
    print("=" * 70)
    return 0 if fail == 0 else 1


def cmd_retry_x1(args):
    """Re-tenta apenas falhas de x1 (cada linha do log: phone|name|status|err)."""
    fail_log = pathlib.Path(args.log).expanduser()
    if not fail_log.is_file():
        print(f"Log de falhas não encontrado: {fail_log}", file=sys.stderr)
        return 1
    text = load_text(args)
    media_data = encode_media(args.image)
    contatos = []
    with open(fail_log, "r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("|")
            if len(parts) >= 1 and parts[0]:
                phone = normalize_phone_e164_br(parts[0])
                if phone:
                    contatos.append({
                        "phone_e164": phone,
                        "name": parts[1] if len(parts) > 1 else "",
                    })
    if not contatos:
        print("Log de falhas x1 vazio.")
        return 0
    print(f"Retry de {len(contatos)} contatos x1...")
    args.confirmed_test = True
    return cmd_x1(args, text, media_data, contatos)


def build_parser():
    p = argparse.ArgumentParser(description="WhatsApp Zappfy — disparo seguro em grupos")
    p.add_argument(
        "mode",
        choices=["preview", "listar", "teste", "broadcast", "x1", "retry", "agendar", "agenda-list", "agenda-cancel", "agenda-watch"],
    )
    p.add_argument("--csv", default=str(DEFAULT_CSV), help="CSV de grupos (broadcast) OU contatos (x1)")
    p.add_argument("--contatos", help="CSV 1:1 com colunas phone,name (alias pra --csv no modo x1)")
    p.add_argument("--x1", action="store_true", help="No `retry`: força modo x1 ao invés de grupos")
    p.add_argument("--csv-out")
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--image")
    p.add_argument("--delay", type=int, default=DEFAULT_DELAY)
    p.add_argument("--jitter", type=float, default=DEFAULT_JITTER, help="0.2 = ±20%%")
    p.add_argument("--retry", type=int, default=DEFAULT_RETRY)
    p.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF)
    p.add_argument("--blacklist", default=str(DEFAULT_BLACKLIST))
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--confirmed-test", action="store_true")
    p.add_argument("--log", help="Caminho custom do log do broadcast OU log de falhas pro retry")
    p.add_argument("--when", help="Data/hora ISO pra agendar (ex: 2026-05-05T14:00)")
    p.add_argument("--job-id", help="ID do job pra agenda-cancel")
    return p


def main():
    args = build_parser().parse_args()

    if args.mode == "listar":
        return cmd_listar(args)
    if args.mode == "agenda-list":
        return cmd_agenda_list()
    if args.mode == "agenda-cancel":
        if not args.job_id:
            print("--job-id obrigatório.", file=sys.stderr)
            return 1
        return cmd_agenda_cancel(args)
    if args.mode == "agenda-watch":
        return cmd_agenda_watch(args)

    if args.mode in ("preview", "teste", "broadcast", "x1", "agendar", "retry"):
        if bool(args.text) == bool(args.text_file):
            print("Use exatamente um entre --text e --text-file.", file=sys.stderr)
            return 1

    try:
        text = load_text(args)
        media_data = encode_media(args.image)
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if args.mode == "teste":
        return cmd_teste(text, media_data)

    if args.mode == "agendar":
        if not args.when:
            print("--when obrigatório (ISO: 2026-05-05T14:00).", file=sys.stderr)
            return 1
        return cmd_agendar(args, text, media_data)

    if args.mode == "retry":
        if not args.log:
            print("--log obrigatório (caminho do falhas_*.log).", file=sys.stderr)
            return 1
        if args.x1 or "x1" in pathlib.Path(args.log).name:
            return cmd_retry_x1(args)
        return cmd_retry(args)

    if args.mode == "x1":
        contatos_path = args.contatos or (
            args.csv if args.csv != str(DEFAULT_CSV) else str(DEFAULT_CONTATOS_CSV)
        )
        try:
            contatos = load_contatos(contatos_path)
        except Exception as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 1
        if not contatos:
            print("ERRO: nenhum contato válido no CSV.", file=sys.stderr)
            return 1
        return cmd_x1(args, text, media_data, contatos)

    try:
        groups = load_groups(args.csv)
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if args.mode == "preview":
        return cmd_preview(args, text, media_data, groups)
    if args.mode == "broadcast":
        return cmd_broadcast(args, text, media_data, groups)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
