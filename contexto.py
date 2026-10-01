#!/usr/bin/env python3
"""
contexto.py — enriquece leads com contexto externo antes do disparo

Fontes suportadas:
  CSV         arquivo local (--source csv --input <path>)
  Sheets      Google Sheets via URL pública CSV (--source sheets --url <csv-url>)
  JSON        endpoint REST que devolve array de objetos (--source json --url <url>)
  HTML        página web — extrai blocos de texto (--source html --url <url>)
              (útil pra puxar contexto sobre uma empresa antes de mensagem x1)

Uso:
  enrich       cruza CSV de contatos com fonte externa via chave (phone/email),
               grava em custom_fields do lead, populando placeholders
               {{ultimo_pedido}}, {{ticket_medio}}, {{empresa}}, etc

  fetch-html   baixa página web, extrai texto/título e salva em custom_fields.contexto_url
               (operador usa em copy x1: "vi seu site sobre {{contexto_url_titulo}}")

  preview      mostra os custom_fields de um lead (debug)
"""

import argparse
import csv
import json
import os
import pathlib
import re
import sys
import urllib.request

from db import connect, init_db, now_iso, upsert_lead
from disparo import normalize_phone_e164_br


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Agent-Whatsapp contexto.py)",
        "Accept": "text/html,application/json,text/csv",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_csv(text):
    import io
    return list(csv.DictReader(io.StringIO(text)))


def parse_json(text):
    data = json.loads(text)
    if isinstance(data, dict):
        return data.get("data", data.get("items", data.get("results", [])))
    return data


def strip_html(html):
    """Extrai texto limpo + title de HTML sem dependência."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    desc_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        html, re.IGNORECASE,
    )
    description = desc_match.group(1).strip() if desc_match else ""

    no_script = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    no_style = re.sub(r"<style[^>]*>.*?</style>", " ", no_script, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", no_style)
    text = re.sub(r"\s+", " ", text).strip()

    return {
        "titulo": title,
        "descricao": description,
        "texto_resumo": text[:500],
    }


def cmd_enrich(args):
    init_db()
    if args.source == "csv":
        rows = parse_csv(pathlib.Path(args.input).read_text(encoding="utf-8"))
    elif args.source == "sheets":
        rows = parse_csv(http_get(args.url))
    elif args.source == "json":
        rows = parse_json(http_get(args.url))
    else:
        print("ERRO: --source deve ser csv, sheets ou json", file=sys.stderr)
        return 1

    if not rows:
        print("Fonte vazia.")
        return 1

    key_field = args.key
    print(f"Cruzando {len(rows)} linhas pela chave '{key_field}'...")

    enriched = 0
    not_found = 0
    with connect() as conn:
        for row in rows:
            raw_key = row.get(key_field, "")
            phone = normalize_phone_e164_br(raw_key) if "phone" in key_field.lower() else None

            if phone:
                cur = conn.execute("SELECT phone_e164, custom_fields FROM leads WHERE phone_e164 = ?", (phone,))
                target = cur.fetchone()
            else:
                target = None

            if not target and args.create_missing:
                upsert_lead(conn, phone or raw_key, source=f"contexto_{args.source}")
                cur = conn.execute("SELECT phone_e164, custom_fields FROM leads WHERE phone_e164 = ?",
                                   (phone or raw_key,))
                target = cur.fetchone()
            if not target:
                not_found += 1
                continue

            try:
                custom = json.loads(target["custom_fields"] or "{}")
            except json.JSONDecodeError:
                custom = {}

            for k, v in row.items():
                if k != key_field:
                    custom[k] = str(v) if v is not None else ""

            conn.execute(
                "UPDATE leads SET custom_fields = ?, updated_at = ? WHERE phone_e164 = ?",
                (json.dumps(custom, ensure_ascii=False), now_iso(), target["phone_e164"]),
            )
            enriched += 1

    print(f"✅ {enriched} leads enriquecidos | {not_found} não encontrados")
    return 0


def cmd_fetch_html(args):
    init_db()
    phone = normalize_phone_e164_br(args.phone)
    if not phone:
        print("ERRO: phone inválido.", file=sys.stderr)
        return 1
    print(f"Baixando {args.url}...")
    try:
        html = http_get(args.url)
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    parsed = strip_html(html)
    print(f"  título: {parsed['titulo'][:80]}")
    print(f"  descricao: {parsed['descricao'][:120]}")
    with connect() as conn:
        cur = conn.execute("SELECT custom_fields FROM leads WHERE phone_e164 = ?", (phone,))
        row = cur.fetchone()
        if not row:
            upsert_lead(conn, phone)
            row = {"custom_fields": "{}"}
        try:
            custom = json.loads(row["custom_fields"] or "{}")
        except Exception:
            custom = {}
        custom["contexto_url"] = args.url
        custom["contexto_url_titulo"] = parsed["titulo"]
        custom["contexto_url_descricao"] = parsed["descricao"]
        custom["contexto_url_resumo"] = parsed["texto_resumo"]
        conn.execute(
            "UPDATE leads SET custom_fields = ?, updated_at = ? WHERE phone_e164 = ?",
            (json.dumps(custom, ensure_ascii=False), now_iso(), phone),
        )
    print(f"✅ contexto salvo em {phone}.custom_fields")
    print("   placeholders disponíveis: {{contexto_url_titulo}}, {{contexto_url_descricao}}, {{contexto_url_resumo}}")
    return 0


def cmd_preview(args):
    init_db()
    phone = normalize_phone_e164_br(args.phone)
    with connect() as conn:
        cur = conn.execute("SELECT name, custom_fields, status, fit_score, tags, notes FROM leads WHERE phone_e164 = ?", (phone,))
        row = cur.fetchone()
    if not row:
        print(f"Lead não encontrado: {phone}", file=sys.stderr)
        return 1
    print(f"phone:       {phone}")
    print(f"name:        {row['name']}")
    print(f"status:      {row['status']}")
    print(f"fit_score:   {row['fit_score']}")
    print(f"tags:        {row['tags']}")
    print(f"notes:       {row['notes'][:200]}")
    try:
        custom = json.loads(row["custom_fields"] or "{}")
    except Exception:
        custom = {}
    print(f"\ncustom_fields ({len(custom)}):")
    for k, v in custom.items():
        v_str = (str(v)[:80] + "...") if len(str(v)) > 80 else str(v)
        print(f"  {{{{{k}}}}} = {v_str}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Enriquece leads com contexto externo")
    sub = p.add_subparsers(dest="cmd", required=True)

    se = sub.add_parser("enrich", help="cruza CSV/Sheets/JSON com leads")
    se.add_argument("--source", required=True, choices=["csv", "sheets", "json"])
    se.add_argument("--input")
    se.add_argument("--url")
    se.add_argument("--key", default="phone", help="coluna chave pra match (default: phone)")
    se.add_argument("--create-missing", action="store_true")

    sf = sub.add_parser("fetch-html", help="baixa página e salva contexto pro lead")
    sf.add_argument("--phone", required=True)
    sf.add_argument("--url", required=True)

    sp = sub.add_parser("preview", help="mostra custom_fields de um lead")
    sp.add_argument("--phone", required=True)

    return p


def main():
    args = build_parser().parse_args()
    return {"enrich": cmd_enrich, "fetch-html": cmd_fetch_html, "preview": cmd_preview}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
