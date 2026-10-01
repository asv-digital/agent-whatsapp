#!/usr/bin/env python3
"""
extrair_leads.py — exporta participantes de grupos WhatsApp para CSV

Modos:
  default        exporta consolidado dos grupos do --csv (ou --all)
  importar       importa lista externa, valida E.164, faz dedup + remove blacklist

Recursos:
  - Normalização E.164 (DDI 55 para BR, validação de DDD 11-99)
  - Hash SHA-256 do número (LGPD-friendly pra logs/auditoria)
  - Dedup cross-grupo (mesmo número em N grupos = 1 linha)
  - Filtro --only-admin
  - Conta nº de grupos onde o número aparece (group_count)
  - Remove blacklist
"""

import argparse
import csv
import hashlib
import pathlib
import re
import sys
import unicodedata
from collections import defaultdict

from disparo import (
    DEFAULT_BLACKLIST,
    DEFAULT_CSV,
    SCRIPT_DIR,
    fetch_groups_from_api,
    load_blacklist,
    load_groups,
)

FIELDNAMES = [
    "lead_phone_e164",
    "lead_phone_hash",
    "lead_display_name",
    "is_admin",
    "is_super_admin",
    "group_count",
    "groups",
]

FIELDNAMES_BY_GROUP = [
    "group_name",
    "group_jid",
    "lead_phone_e164",
    "lead_phone_hash",
    "lead_display_name",
    "is_admin",
    "is_super_admin",
]

DDDS_BR_VALIDOS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19,
    21, 22, 24, 27, 28,
    31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49,
    51, 53, 54, 55,
    61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79,
    81, 82, 83, 84, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 96, 97, 98, 99,
}


def hash_phone(phone):
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]


def normalize_e164_br(raw):
    """Normaliza pra E.164 BR (55 + DDD + número). Retorna None se inválido."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("55") and len(digits) in (12, 13):
        ddd = digits[2:4]
    elif len(digits) in (10, 11):
        digits = "55" + digits
        ddd = digits[2:4]
    else:
        return digits if len(digits) >= 10 else None

    try:
        ddd_int = int(ddd)
        if ddd_int not in DDDS_BR_VALIDOS:
            return digits
    except ValueError:
        return digits
    return digits


def slugify(value):
    n = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(c if c.isalnum() else "_" for c in n.lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "grupo"


def collect_participants(api_groups, csv_groups=None, only_admin=False, blacklist=None):
    """Retorna dict[phone] = {name, is_admin, is_super, groups[]}."""
    if blacklist is None:
        blacklist = set()
    leads = defaultdict(lambda: {
        "name": "",
        "is_admin": False,
        "is_super": False,
        "groups": [],
    })

    if csv_groups:
        target_jids = {g["jid"] for g in csv_groups}
        target_names = {g["name"] for g in csv_groups}
        api_groups = [g for g in api_groups if g.get("JID") in target_jids or g.get("Name") in target_names]

    for group in api_groups:
        group_name = group.get("Name", "(sem nome)")
        for participant in group.get("Participants", []):
            raw_phone = participant.get("PhoneNumber", "")
            phone = normalize_e164_br(raw_phone)
            if not phone:
                continue
            digits = re.sub(r"\D", "", phone)
            if digits in blacklist:
                continue
            is_admin = bool(participant.get("IsAdmin"))
            is_super = bool(participant.get("IsSuperAdmin"))
            if only_admin and not (is_admin or is_super):
                continue
            entry = leads[phone]
            entry["name"] = entry["name"] or participant.get("DisplayName", "")
            entry["is_admin"] = entry["is_admin"] or is_admin
            entry["is_super"] = entry["is_super"] or is_super
            entry["groups"].append(group_name)
    return leads


def export_consolidated_dedup(api_groups, csv_groups, args, blacklist):
    leads = collect_participants(api_groups, csv_groups, args.only_admin, blacklist)
    output = pathlib.Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDNAMES)
        writer.writeheader()
        for phone, data in sorted(leads.items()):
            writer.writerow({
                "lead_phone_e164": phone,
                "lead_phone_hash": hash_phone(phone),
                "lead_display_name": data["name"],
                "is_admin": data["is_admin"],
                "is_super_admin": data["is_super"],
                "group_count": len(data["groups"]),
                "groups": " | ".join(data["groups"]),
            })
    print(f"CSV: {output}")
    print(f"Leads únicos: {len(leads)}")
    return 0


def export_consolidated_raw(api_groups, csv_groups, args, blacklist):
    """Sem dedup — uma linha por (grupo, lead)."""
    output = pathlib.Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    if csv_groups:
        target_jids = {g["jid"] for g in csv_groups}
        api_groups = [g for g in api_groups if g.get("JID") in target_jids]

    rows = 0
    with open(output, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDNAMES_BY_GROUP)
        writer.writeheader()
        for group in api_groups:
            for participant in group.get("Participants", []):
                phone = normalize_e164_br(participant.get("PhoneNumber", ""))
                if not phone:
                    continue
                digits = re.sub(r"\D", "", phone)
                if digits in blacklist:
                    continue
                is_admin = bool(participant.get("IsAdmin"))
                is_super = bool(participant.get("IsSuperAdmin"))
                if args.only_admin and not (is_admin or is_super):
                    continue
                writer.writerow({
                    "group_name": group.get("Name", ""),
                    "group_jid": group.get("JID", ""),
                    "lead_phone_e164": phone,
                    "lead_phone_hash": hash_phone(phone),
                    "lead_display_name": participant.get("DisplayName", ""),
                    "is_admin": is_admin,
                    "is_super_admin": is_super,
                })
                rows += 1
    print(f"CSV: {output}")
    print(f"Linhas: {rows}")
    return rows


def export_split(api_groups, csv_groups, args, blacklist):
    output_dir = pathlib.Path(args.split_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    if csv_groups:
        target = csv_groups
    else:
        target = [{"name": g.get("Name", "(sem nome)"), "jid": g.get("JID", "")} for g in api_groups]

    api_by_jid = {g.get("JID"): g for g in api_groups}
    api_by_name = {g.get("Name"): g for g in api_groups}

    files_written = []
    rows_total = 0
    unmatched = []

    for index, t in enumerate(target, 1):
        api_g = api_by_jid.get(t["jid"]) or api_by_name.get(t["name"])
        if not api_g:
            unmatched.append(t)
            continue
        filename = f"{index:02d}_{slugify(t['name'])}.csv"
        out = output_dir / filename
        rows = 0
        with open(out, "w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=FIELDNAMES_BY_GROUP)
            writer.writeheader()
            for participant in api_g.get("Participants", []):
                phone = normalize_e164_br(participant.get("PhoneNumber", ""))
                if not phone:
                    continue
                digits = re.sub(r"\D", "", phone)
                if digits in blacklist:
                    continue
                is_admin = bool(participant.get("IsAdmin"))
                is_super = bool(participant.get("IsSuperAdmin"))
                if args.only_admin and not (is_admin or is_super):
                    continue
                writer.writerow({
                    "group_name": t["name"],
                    "group_jid": t["jid"],
                    "lead_phone_e164": phone,
                    "lead_phone_hash": hash_phone(phone),
                    "lead_display_name": participant.get("DisplayName", ""),
                    "is_admin": is_admin,
                    "is_super_admin": is_super,
                })
                rows += 1
        files_written.append((t["name"], out, rows))
        rows_total += rows

    print(f"CSVs: {len(files_written)} em {output_dir}")
    print(f"Linhas totais: {rows_total}")
    for name, path, rows in files_written:
        print(f"  - {name}: {rows} → {path}")
    if unmatched:
        print("\nGrupos sem match:", file=sys.stderr)
        for t in unmatched:
            print(f"  - {t['name']} ({t['jid']})", file=sys.stderr)
    return rows_total


def cmd_default(args):
    blacklist = load_blacklist(args.blacklist) if args.blacklist else set()
    api_groups = fetch_groups_from_api()
    csv_groups = None if args.all else load_groups(args.csv)

    if args.split_dir:
        return export_split(api_groups, csv_groups, args, blacklist)
    if args.dedup:
        return export_consolidated_dedup(api_groups, csv_groups, args, blacklist)
    return export_consolidated_raw(api_groups, csv_groups, args, blacklist)


def cmd_importar(args):
    """Importa CSV externo, normaliza E.164, dedup, remove blacklist."""
    src = pathlib.Path(args.input).expanduser()
    if not src.is_file():
        print(f"Arquivo não encontrado: {src}", file=sys.stderr)
        return 1
    blacklist = load_blacklist(args.blacklist) if args.blacklist else set()

    out = pathlib.Path(args.merge or args.output or f"./leads_importados.csv").expanduser()
    seen = set()

    if args.merge and out.is_file():
        with open(out, "r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                phone = row.get("lead_phone_e164") or row.get("phone")
                if phone:
                    seen.add(re.sub(r"\D", "", phone))

    imported = 0
    dedup = 0
    blacklisted = 0
    invalid = 0

    rows_to_write = []
    if args.merge and out.is_file():
        with open(out, "r", encoding="utf-8", newline="") as fp:
            rows_to_write = list(csv.DictReader(fp))

    with open(src, "r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            raw = row.get("phone") or row.get("lead_phone_e164") or row.get("lead_phone") or row.get("whatsapp") or ""
            phone = normalize_e164_br(raw)
            if not phone:
                invalid += 1
                continue
            digits = re.sub(r"\D", "", phone)
            if digits in blacklist:
                blacklisted += 1
                continue
            if digits in seen:
                dedup += 1
                continue
            seen.add(digits)
            rows_to_write.append({
                "lead_phone_e164": phone,
                "lead_phone_hash": hash_phone(phone),
                "lead_display_name": row.get("name") or row.get("lead_display_name") or "",
                "is_admin": False,
                "is_super_admin": False,
                "group_count": 0,
                "groups": "imported",
            })
            imported += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows_to_write:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"✅ {imported} importados | {dedup} dedup | {blacklisted} blacklist | {invalid} inválidos")
    print(f"Total no arquivo: {len(rows_to_write)} ({out})")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Extrai leads de grupos WhatsApp")
    sub = p.add_subparsers(dest="cmd")

    default = sub.add_parser("export", help="(default) extrai leads dos grupos")
    default.add_argument("--csv", default=str(DEFAULT_CSV))
    default.add_argument("--all", action="store_true", help="todos grupos da instância")
    default.add_argument("--output")
    default.add_argument("--split-dir")
    default.add_argument("--dedup", action="store_true", help="consolida 1 linha por número")
    default.add_argument("--only-admin", action="store_true")
    default.add_argument("--blacklist", default=str(DEFAULT_BLACKLIST))

    imp = sub.add_parser("importar")
    imp.add_argument("--input", required=True)
    imp.add_argument("--output")
    imp.add_argument("--merge", help="path do CSV existente pra mergear (dedup automático)")
    imp.add_argument("--blacklist", default=str(DEFAULT_BLACKLIST))

    p.add_argument("--csv", default=str(DEFAULT_CSV))
    p.add_argument("--all", action="store_true")
    p.add_argument("--output")
    p.add_argument("--split-dir")
    p.add_argument("--dedup", action="store_true")
    p.add_argument("--only-admin", action="store_true")
    p.add_argument("--blacklist", default=str(DEFAULT_BLACKLIST))
    return p


def main():
    args = build_parser().parse_args()
    if args.cmd == "importar":
        return cmd_importar(args)

    if not args.output and not args.split_dir:
        print("Use --output OU --split-dir.", file=sys.stderr)
        return 1
    if args.output and args.split_dir:
        print("Use apenas um: --output OU --split-dir.", file=sys.stderr)
        return 1
    return cmd_default(args)


if __name__ == "__main__":
    raise SystemExit(main())
