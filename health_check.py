#!/usr/bin/env python3
"""
health_check.py — diagnóstico da instância Zappfy

Mede:
  - Latência média da API (10 chamadas)
  - Status atual da instância (200 OK = saudável)
  - Nº de grupos visíveis
  - Taxa de erro nas últimas 24h (lê logs/disparo_*.log)
  - Último broadcast (data + sucesso)

Saída: 4 linhas com 🟢 / 🟡 / 🔴 + recomendação.
"""

import pathlib
import re
import statistics
import sys
import time
from datetime import datetime, timedelta

from disparo import API_BASE, LOGS_DIR, TOKEN, api_request


def latency_test(n=10):
    if not TOKEN:
        return None, None
    samples = []
    fails = 0
    for _ in range(n):
        t0 = time.time()
        r = api_request("GET", "/group/list", timeout=10)
        elapsed = (time.time() - t0) * 1000
        if r["ok"]:
            samples.append(elapsed)
        else:
            fails += 1
    if not samples:
        return None, fails
    return statistics.mean(samples), fails


def count_groups():
    r = api_request("GET", "/group/list", timeout=15)
    if not r["ok"]:
        return None
    body = r["body"]
    if isinstance(body, dict):
        return len(body.get("groups", []))
    if isinstance(body, list):
        return len(body)
    return None


def parse_24h_error_rate():
    cutoff = datetime.now() - timedelta(hours=24)
    ok = 0
    fail = 0
    if not LOGS_DIR.is_dir():
        return None, None, None
    for log in LOGS_DIR.glob("disparo_*.log"):
        if datetime.fromtimestamp(log.stat().st_mtime) < cutoff:
            continue
        try:
            with open(log, "r", encoding="utf-8") as fp:
                for line in fp:
                    parts = line.strip().split("|")
                    if len(parts) >= 3:
                        if parts[2] == "OK":
                            ok += 1
                        elif parts[2] == "FAIL":
                            fail += 1
        except Exception:
            continue
    total = ok + fail
    rate = (fail / total * 100) if total else 0
    return total, fail, rate


def last_broadcast():
    if not LOGS_DIR.is_dir():
        return None, None, None
    logs = sorted(LOGS_DIR.glob("disparo_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return None, None, None
    log = logs[0]
    ok = fail = 0
    with open(log, "r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("|")
            if len(parts) >= 3:
                if parts[2] == "OK":
                    ok += 1
                elif parts[2] == "FAIL":
                    fail += 1
    return log, ok, fail


def main():
    if not TOKEN:
        print("🔴 ZAPPFY_TOKEN ausente no .env", file=sys.stderr)
        print("   Ainda não tem a API do WhatsApp? Contrate em https://zappfy.io e copie a API Key", file=sys.stderr)
        print("   da instância (passo a passo: COMO-INSTALAR.md, seção 1.1).", file=sys.stderr)
        return 2

    print("=== Health Check — whatsapp-zappfy-grupos ===")
    print(f"API: {API_BASE}")

    avg_ms, fails_in_test = latency_test(10)
    if avg_ms is None:
        print(f"🔴 API: instância não responde ({fails_in_test}/10 falhas) — verificar QR/Zappfy")
        return 1
    if avg_ms < 800:
        print(f"🟢 API: 200 OK · latência {avg_ms:.0f}ms ({fails_in_test}/10 falhas)")
    elif avg_ms < 2000:
        print(f"🟡 API: lenta · latência {avg_ms:.0f}ms ({fails_in_test}/10 falhas)")
    else:
        print(f"🔴 API: muito lenta · latência {avg_ms:.0f}ms — considere pausar disparos")

    n_groups = count_groups()
    if n_groups is None:
        print("🔴 Grupos: falha ao consultar")
    elif n_groups == 0:
        print("🟡 Grupos visíveis: 0 — instância sem grupos ou desconectada")
    else:
        print(f"🟢 Grupos visíveis: {n_groups}")

    total_24h, fail_24h, rate_24h = parse_24h_error_rate()
    if total_24h is None or total_24h == 0:
        print("⚪ Sem disparos nas últimas 24h")
    elif rate_24h < 2:
        print(f"🟢 Erro 24h: {rate_24h:.1f}% ({fail_24h}/{total_24h})")
    elif rate_24h < 5:
        print(f"🟡 Erro 24h: {rate_24h:.1f}% ({fail_24h}/{total_24h}) — monitore")
    else:
        print(f"🔴 Erro 24h: {rate_24h:.1f}% ({fail_24h}/{total_24h}) — pause 24h")

    log, ok, fail = last_broadcast()
    if log is None:
        print("⚪ Último broadcast: nenhum")
    else:
        ts = datetime.fromtimestamp(log.stat().st_mtime)
        print(f"📋 Último broadcast: {ts:%Y-%m-%d %H:%M} · {ok} OK · {fail} falhas · {log.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
