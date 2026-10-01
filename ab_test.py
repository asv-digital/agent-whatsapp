#!/usr/bin/env python3
"""
ab_test.py — A/B test de copy em grupos WhatsApp via Zappfy

Comandos:
  split    distribui grupos do CSV em A (50%) e B (50%) e dispara cada copy
  apurar   após N horas, calcula taxa de resposta de cada bracket e declara winner

Apuração simplificada: conta nº de mensagens de RESPOSTA recebidas no grupo
após o disparo (proxy de engajamento). Para apuração de conversão real, use
URLs com UTM e meça no GA4 (não cabe nesse script).

Significância: usa teste de proporção (z-test) com nível 95%. Se p>0.05, declara
"sem significância — empate".
"""

import argparse
import csv
import json
import math
import pathlib
import random
import sys
from datetime import datetime, timedelta

from disparo import (
    DEFAULT_CSV,
    LOGS_DIR,
    SCRIPT_DIR,
    api_request,
    encode_media,
    fetch_groups_from_api,
    load_groups,
    load_text,
    send_with_retry,
    now_iso,
)

CAMPAIGNS_DIR = SCRIPT_DIR / "campaigns"


def cmd_split(args):
    LOGS_DIR.mkdir(exist_ok=True)
    CAMPAIGNS_DIR.mkdir(exist_ok=True)

    if not args.confirmed_test:
        print("Use --confirmed-test após validar copy A e B no número pessoal.", file=sys.stderr)
        return 1

    copy_a = pathlib.Path(args.copy_a).read_text(encoding="utf-8")
    copy_b = pathlib.Path(args.copy_b).read_text(encoding="utf-8")
    media_a = encode_media(args.image_a)
    media_b = encode_media(args.image_b)

    groups = load_groups(args.csv)
    if len(groups) < 4:
        print("ERRO: A/B precisa de ≥4 grupos pra ter sample mínimo.", file=sys.stderr)
        return 1

    rnd = random.Random(args.seed) if args.seed else random.Random()
    shuffled = groups[:]
    rnd.shuffle(shuffled)
    half = len(shuffled) // 2
    bracket_a = shuffled[:half]
    bracket_b = shuffled[half:]

    campaign_id = f"ab_{datetime.now():%Y%m%d_%H%M%S}"
    log_a = LOGS_DIR / f"{campaign_id}_A.log"
    log_b = LOGS_DIR / f"{campaign_id}_B.log"

    print(f"=== A/B {campaign_id} ===")
    print(f"A: {len(bracket_a)} grupos | B: {len(bracket_b)} grupos")
    print(f"Janela apuração: {args.window_hours}h")
    print(f"Disparando A...")

    import time as _t

    def _broadcast(bracket, copy, media, log_path, label):
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(f"# A/B campaign={campaign_id} bracket={label} ts={now_iso()}\n")
            for i, group in enumerate(bracket, 1):
                ok, status, err, attempts = send_with_retry(
                    group["jid"], copy, media, True, args.retry, args.backoff, fp
                )
                tag = "OK" if ok else f"FAIL({status})"
                print(f"  [{label}][{i}/{len(bracket)}] {group['name'][:30]} {tag}")
                if i < len(bracket):
                    _t.sleep(args.delay)

    _broadcast(bracket_a, copy_a, media_a, log_a, "A")
    print(f"Disparando B...")
    _broadcast(bracket_b, copy_b, media_b, log_b, "B")

    campaign = {
        "id": campaign_id,
        "started_at": now_iso(),
        "window_hours": args.window_hours,
        "bracket_a": [{"name": g["name"], "jid": g["jid"]} for g in bracket_a],
        "bracket_b": [{"name": g["name"], "jid": g["jid"]} for g in bracket_b],
        "log_a": str(log_a),
        "log_b": str(log_b),
        "apurar_after": (datetime.now() + timedelta(hours=args.window_hours)).isoformat(),
    }
    campaign_path = CAMPAIGNS_DIR / f"{campaign_id}.json"
    with open(campaign_path, "w", encoding="utf-8") as fp:
        json.dump(campaign, fp, indent=2)

    print(f"\n✅ campanha {campaign_id} disparada")
    print(f"📊 apurar em: {campaign['apurar_after']}")
    print(f"   `python3 ab_test.py apurar --campaign {campaign_id}`")
    return 0


def fetch_messages_for_group(jid):
    """Histórico do grupo pela API da Zappfy (POST /message/find)."""
    r = api_request("POST", "/message/find", {"chatid": jid, "limit": 200})
    if r["ok"]:
        body = r["body"]
        if isinstance(body, dict):
            return body.get("messages", [])
        if isinstance(body, list):
            return body
    return []


def count_replies_after(jid, since_iso):
    """Conta mensagens recebidas (não da própria instância) após since_iso."""
    msgs = fetch_messages_for_group(jid)
    if not msgs:
        return None
    since_ts = datetime.fromisoformat(since_iso).timestamp()
    count = 0
    for m in msgs:
        try:
            ts = m.get("messageTimestamp") or m.get("timestamp") or m.get("Timestamp") or m.get("MessageTimestamp")
            ts = float(ts) if ts else 0
            if ts > 1e11:  # a Zappfy devolve em milissegundos
                ts = ts / 1000.0
            from_me = m.get("fromMe") or m.get("FromMe")
            if not from_me and ts >= since_ts:
                count += 1
        except Exception:
            continue
    return count


def proportion_test(x_a, n_a, x_b, n_b):
    """Z-test de proporções. Retorna (winner, p_value)."""
    if n_a == 0 or n_b == 0:
        return None, 1.0
    p_a = x_a / n_a
    p_b = x_b / n_b
    p_pool = (x_a + x_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None, 1.0
    z = (p_a - p_b) / se
    p_value = 2 * (1 - _phi(abs(z)))
    if p_value > 0.05:
        return None, p_value
    return ("A" if p_a > p_b else "B"), p_value


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cmd_apurar(args):
    cp = CAMPAIGNS_DIR / f"{args.campaign}.json"
    if not cp.is_file():
        print(f"Campanha não encontrada: {args.campaign}", file=sys.stderr)
        return 1
    with open(cp, "r", encoding="utf-8") as fp:
        c = json.load(fp)

    print(f"=== APURAÇÃO {args.campaign} ===")
    started = c["started_at"]

    def _bracket_stats(bracket):
        total_replies = 0
        groups_with_data = 0
        for g in bracket:
            n = count_replies_after(g["jid"], started)
            if n is not None:
                total_replies += n
                groups_with_data += 1
        return total_replies, groups_with_data, len(bracket)

    rep_a, gd_a, n_a = _bracket_stats(c["bracket_a"])
    rep_b, gd_b, n_b = _bracket_stats(c["bracket_b"])

    rate_a = (rep_a / n_a) if n_a else 0
    rate_b = (rep_b / n_b) if n_b else 0

    print(f"A: {rep_a} respostas em {n_a} grupos ({gd_a} com dados) → média {rate_a:.2f} resp/grupo")
    print(f"B: {rep_b} respostas em {n_b} grupos ({gd_b} com dados) → média {rate_b:.2f} resp/grupo")

    winner, p = proportion_test(rep_a, n_a * 50, rep_b, n_b * 50)
    if winner is None:
        print(f"\n🤝 sem significância (p={p:.3f}) — empate, escolha por feeling.")
    else:
        delta = abs(rate_a - rate_b)
        print(f"\n🏆 winner: {winner} (p={p:.3f})  Δ={delta:.2f} resp/grupo")

    out = LOGS_DIR / f"apuracao_{args.campaign}.md"
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(f"# Apuração A/B {args.campaign}\n\n")
        fp.write(f"- Início: {started}\n")
        fp.write(f"- Janela: {c['window_hours']}h\n\n")
        fp.write(f"| Bracket | Grupos | Respostas | Média |\n|---|---|---|---|\n")
        fp.write(f"| A | {n_a} | {rep_a} | {rate_a:.2f} |\n")
        fp.write(f"| B | {n_b} | {rep_b} | {rate_b:.2f} |\n\n")
        fp.write(f"Resultado: {'winner ' + winner if winner else 'empate'} (p={p:.3f})\n")
    print(f"Relatório: {out}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="A/B test de copy em grupos")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split")
    sp.add_argument("--copy-a", required=True)
    sp.add_argument("--copy-b", required=True)
    sp.add_argument("--image-a")
    sp.add_argument("--image-b")
    sp.add_argument("--csv", default=str(DEFAULT_CSV))
    sp.add_argument("--window-hours", type=int, default=4)
    sp.add_argument("--delay", type=int, default=60)
    sp.add_argument("--retry", type=int, default=3)
    sp.add_argument("--backoff", type=float, default=2)
    sp.add_argument("--seed", type=int)
    sp.add_argument("--confirmed-test", action="store_true")

    ap = sub.add_parser("apurar")
    ap.add_argument("--campaign", required=True)
    return p


def main():
    args = build_parser().parse_args()
    if args.cmd == "split":
        return cmd_split(args)
    if args.cmd == "apurar":
        return cmd_apurar(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
