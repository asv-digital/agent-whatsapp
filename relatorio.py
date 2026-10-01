#!/usr/bin/env python3
"""
relatorio.py — relatório executivo de campanha (markdown publicável)

Modos:
  --log <path>      gera relatório do disparo específico
  --week            agrega todos os logs da semana em um único relatório
  --campaign <id>   relatório de campanha A/B (junta A_log + B_log + apuração)

Saída: arquivo .md pronto pra abrir, copiar pra cliente, anexar em e-mail.
"""

import argparse
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from disparo import LOGS_DIR


def parse_log(log_path):
    """Lê log estruturado do disparo.py e retorna stats."""
    stats = {
        "total": 0,
        "ok": 0,
        "fail": 0,
        "retry_ok": 0,
        "fail_by_status": Counter(),
        "fail_by_reason": Counter(),
        "started_at": None,
        "ended_at": None,
        "config": {},
        "groups": [],
    }
    if not log_path.is_file():
        return stats
    seen_numbers = {}
    with open(log_path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line.startswith("#"):
                m = re.search(r"groups=(\d+).*delay=(\d+).*jitter=([\d.]+).*retry=(\d+)", line)
                if m:
                    stats["config"] = {
                        "groups_planned": int(m.group(1)),
                        "delay": int(m.group(2)),
                        "jitter": float(m.group(3)),
                        "retry": int(m.group(4)),
                    }
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            ts, number, kind = parts[0], parts[1], parts[2]
            try:
                dt = datetime.fromisoformat(ts)
                if not stats["started_at"] or dt < stats["started_at"]:
                    stats["started_at"] = dt
                if not stats["ended_at"] or dt > stats["ended_at"]:
                    stats["ended_at"] = dt
            except Exception:
                pass
            prev = seen_numbers.get(number)
            if kind == "OK":
                stats["ok"] += 1
                if prev == "RETRY":
                    stats["retry_ok"] += 1
                seen_numbers[number] = "OK"
            elif kind == "FAIL":
                stats["fail"] += 1
                seen_numbers[number] = "FAIL"
                for p in parts:
                    if p.startswith("status="):
                        stats["fail_by_status"][p.split("=", 1)[1]] += 1
                    if p.startswith("err="):
                        reason = p.split("=", 1)[1][:50]
                        stats["fail_by_reason"][reason or "(sem mensagem)"] += 1
            elif kind == "RETRY":
                seen_numbers[number] = "RETRY"
    stats["total"] = stats["ok"] + stats["fail"]
    return stats


def render_report(stats, log_path, ticket_value=None, response_rate=0.08, conversion=0.12):
    """Gera markdown."""
    lines = []
    lines.append(f"# Relatório de disparo — {log_path.name}\n")
    if stats["started_at"]:
        lines.append(f"- **Início:** {stats['started_at']:%Y-%m-%d %H:%M:%S}")
    if stats["ended_at"]:
        lines.append(f"- **Fim:** {stats['ended_at']:%Y-%m-%d %H:%M:%S}")
    if stats["started_at"] and stats["ended_at"]:
        elapsed = stats["ended_at"] - stats["started_at"]
        lines.append(f"- **Duração:** {elapsed.total_seconds() / 60:.1f} min")
    if stats["config"]:
        c = stats["config"]
        lines.append(f"- **Config:** {c.get('groups_planned')} grupos | delay {c.get('delay')}s ±{int(c.get('jitter',0)*100)}% | retry {c.get('retry')}")
    lines.append("")

    lines.append("## Resumo executivo\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Grupos atingidos | {stats['total']} |")
    lines.append(f"| ✅ Sucesso | {stats['ok']} ({stats['ok']/stats['total']*100:.1f}%)" if stats['total'] else "| ✅ Sucesso | 0 |")
    lines.append(f"| ❌ Falhas | {stats['fail']} ({stats['fail']/stats['total']*100:.1f}%)" if stats['total'] else "| ❌ Falhas | 0 |")
    lines.append(f"| 🔁 Recuperados via retry | {stats['retry_ok']} |")
    lines.append("")

    if stats["fail_by_status"]:
        lines.append("## Falhas por código HTTP\n")
        lines.append("| Status | Ocorrências | Diagnóstico |")
        lines.append("|---|---|---|")
        diagnostics = {
            "401": "Token inválido — verifique .env",
            "403": "Sem permissão (grupo só admin posta?)",
            "404": "JID inexistente — grupo apagado/saiu",
            "429": "Rate limit — aumente --delay",
            "500": "Erro Zappfy — tentar mais tarde",
            "503": "Zappfy indisponível",
            "0":   "Sem resposta — instância caiu?",
        }
        for status, count in stats["fail_by_status"].most_common():
            diag = diagnostics.get(status, "—")
            lines.append(f"| {status} | {count} | {diag} |")
        lines.append("")

    if stats["fail_by_reason"]:
        lines.append("## Top 5 motivos de falha\n")
        for reason, count in stats["fail_by_reason"].most_common(5):
            lines.append(f"- **{count}x** `{reason}`")
        lines.append("")

    quality_pct = (stats['fail'] / stats['total'] * 100) if stats['total'] else 0
    if quality_pct < 2:
        quality_emoji = "🟢"
        quality_text = "Excelente — instância saudável."
    elif quality_pct < 5:
        quality_emoji = "🟡"
        quality_text = "Atenção — taxa de falha acima do esperado, monitore."
    else:
        quality_emoji = "🔴"
        quality_text = "Crítico — instância pode estar bloqueada/banida. Pause disparos por 24h."
    lines.append(f"## Qualidade da instância (proxy)\n")
    lines.append(f"{quality_emoji} **Taxa de falha: {quality_pct:.1f}%** — {quality_text}\n")

    if ticket_value and stats["ok"]:
        lines.append("## ROI estimado\n")
        leads_alcanc = stats["ok"] * 80
        respostas = leads_alcanc * response_rate
        vendas = respostas * conversion
        receita = vendas * ticket_value
        lines.append(f"- Leads alcançados (estimativa 80/grupo): **{leads_alcanc:,}**")
        lines.append(f"- Taxa de resposta esperada (8%): **{respostas:.0f}**")
        lines.append(f"- Conversão sobre respostas (12%): **{vendas:.1f} vendas**")
        lines.append(f"- Receita potencial × ticket R$ {ticket_value}: **R$ {receita:,.2f}**")
        lines.append("")

    lines.append("## Próximas ações sugeridas\n")
    if stats["fail"] > 0:
        lines.append(f"1. Rodar retry pontual: `python3 disparo.py retry --log logs/falhas_<ts>.log --text-file <copy>`")
    if quality_pct >= 5:
        lines.append("2. Pausar disparos por 24h e rodar `python3 health_check.py`.")
    if stats["fail_by_status"].get("404", 0) >= 3:
        lines.append("3. Limpar grupos.csv removendo JIDs com status 404 (grupos inexistentes).")
    if stats["fail_by_status"].get("429", 0) >= 1:
        lines.append("4. Aumentar `--delay` de 60 para 90s no próximo disparo.")
    lines.append("5. Auditar opt-out recebidos no WhatsApp e adicionar à `blacklist.txt`.")
    lines.append("")

    lines.append(f"---\n_Gerado por Agent-Whatsapp · {datetime.now():%Y-%m-%d %H:%M}_\n")
    return "\n".join(lines)


def cmd_single(args):
    log_path = pathlib.Path(args.log).expanduser()
    if not log_path.is_file():
        print(f"Log não encontrado: {log_path}", file=sys.stderr)
        return 1
    stats = parse_log(log_path)
    md = render_report(stats, log_path, ticket_value=args.ticket, response_rate=args.response_rate, conversion=args.conversion)
    out = pathlib.Path(args.output).expanduser() if args.output else log_path.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    print(f"✅ relatório: {out}")
    print(f"   {stats['ok']}/{stats['total']} OK | {stats['fail']} falhas | {stats['retry_ok']} retry-ok")
    return 0


def cmd_week(args):
    LOGS_DIR.mkdir(exist_ok=True)
    cutoff = datetime.now() - timedelta(days=7)
    logs = []
    for p in LOGS_DIR.glob("disparo_*.log"):
        if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff:
            logs.append(p)
    if not logs:
        print("Nenhum log na última semana.")
        return 0

    agg = {"total": 0, "ok": 0, "fail": 0, "retry_ok": 0, "fail_by_status": Counter()}
    for log in logs:
        s = parse_log(log)
        agg["total"] += s["total"]
        agg["ok"] += s["ok"]
        agg["fail"] += s["fail"]
        agg["retry_ok"] += s["retry_ok"]
        for k, v in s["fail_by_status"].items():
            agg["fail_by_status"][k] += v

    out = pathlib.Path(args.output).expanduser() if args.output else LOGS_DIR / f"relatorio_semana_{datetime.now():%Y-W%V}.md"
    md = []
    md.append(f"# Relatório semanal — {datetime.now():%Y semana %V}\n")
    md.append(f"- Disparos analisados: {len(logs)}")
    md.append(f"- Mensagens totais: {agg['total']}")
    md.append(f"- ✅ OK: {agg['ok']}  | ❌ Falhas: {agg['fail']}  | 🔁 Retry-ok: {agg['retry_ok']}")
    if agg["total"]:
        md.append(f"- Taxa de sucesso: {agg['ok']/agg['total']*100:.1f}%")
    md.append("\n## Disparos da semana\n")
    md.append("| Log | OK | Falhas | Retry-ok |")
    md.append("|---|---|---|---|")
    for log in sorted(logs, reverse=True):
        s = parse_log(log)
        md.append(f"| {log.name} | {s['ok']} | {s['fail']} | {s['retry_ok']} |")
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"✅ relatório semanal: {out}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Relatório executivo de campanha")
    p.add_argument("--log", help="Path do log de um disparo único")
    p.add_argument("--week", action="store_true", help="Agrega logs da última semana")
    p.add_argument("--output", help="Path do .md de saída")
    p.add_argument("--ticket", type=float, help="Ticket médio em R$ pra estimar ROI")
    p.add_argument("--response-rate", type=float, default=0.08)
    p.add_argument("--conversion", type=float, default=0.12)
    args = p.parse_args()

    if args.week:
        return cmd_week(args)
    if args.log:
        return cmd_single(args)
    print("Use --log <path> ou --week.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
