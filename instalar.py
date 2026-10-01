#!/usr/bin/env python3
"""
instalar.py — assistente de instalação do Agent-Whatsapp

Faz as perguntas, preenche o arquivo de configuração (.env), testa a conexão com a
Zappfy, puxa a lista dos seus grupos e instala o agente no Claude Code.
Funciona igual no Mac, Windows e Linux.

Uso:
  python3 instalar.py

Modo sem perguntas (pra automação/teste):
  python3 instalar.py --token <API_KEY> --numero 5511999998888 [--agenda URL] [--empresa NOME] --sim
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
ENV_EXAMPLE = SCRIPT_DIR / ".env.example"
AGENT_FILE = SCRIPT_DIR / "agent-whatsapp.md"
PLACEHOLDER_TOKEN = "cole-aqui-a-api-key-da-sua-instancia"
PLACEHOLDER_NUMBER = "5511999998888"


def read_env():
    """Lê o .env atual como dict (só pra mostrar o que já está preenchido)."""
    values = {}
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def set_env_values(updates):
    """Grava os valores no .env: troca a linha da chave se existir, senão acrescenta no fim."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pending = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in pending:
            lines[i] = f"{key}={format_value(pending.pop(key))}"
    for key, value in pending.items():
        lines.append(f"{key}={format_value(value)}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_value(value):
    value = str(value)
    return f'"{value}"' if "#" in value else value


def only_digits(text):
    return "".join(c for c in text if c.isdigit())


def normalize_number(text):
    """Deixa o número no formato 55 + DDD + número, só dígitos. '' se não parecer válido."""
    digits = only_digits(text)
    if len(digits) in (10, 11):
        digits = "55" + digits
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    return ""


def ask(question, default="", required=False, validate=None):
    while True:
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{question}{suffix}: ").strip()
        except EOFError:
            answer = ""
        if not answer:
            answer = default
        if validate and answer:
            fixed = validate(answer)
            if not fixed:
                print("   Não entendi esse valor. Tente de novo.")
                continue
            answer = fixed
        if required and not answer:
            print("   Esse campo é obrigatório.")
            continue
        return answer


def run_script(*args):
    """Roda outro script do agente mostrando a saída na tela. Retorna True se deu certo."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, *args], cwd=str(SCRIPT_DIR), env=env)
    return result.returncode == 0


def install_agent():
    target_dir = SCRIPT_DIR / ".claude" / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(AGENT_FILE, target_dir / AGENT_FILE.name)
    return target_dir / AGENT_FILE.name


def main():
    parser = argparse.ArgumentParser(description="Assistente de instalação do Agent-Whatsapp")
    parser.add_argument("--token", help="API Key da instância na Zappfy")
    parser.add_argument("--numero", help="Seu WhatsApp com DDD (só números)")
    parser.add_argument("--agenda", help="Link da sua agenda (Calendly ou parecido)")
    parser.add_argument("--empresa", help="Nome da sua empresa")
    parser.add_argument("--sim", action="store_true", help="Não pergunta nada: usa os valores passados")
    args = parser.parse_args()

    print("=" * 62)
    print(" Agent-Whatsapp — assistente de instalação")
    print("=" * 62)

    if not ENV_PATH.is_file():
        if not ENV_EXAMPLE.is_file():
            print("ERRO: não achei o arquivo .env.example nesta pasta.")
            print("Abra o Terminal dentro da pasta do agente e rode de novo.")
            return 1
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        print("Criei o seu arquivo de configuração (.env).")
    else:
        print("Já existe um arquivo de configuração (.env). Vou só atualizar o que você responder.")

    current = read_env()
    cur_token = current.get("ZAPPFY_TOKEN", "")
    if cur_token == PLACEHOLDER_TOKEN:
        cur_token = ""
    cur_number = current.get("TEST_NUMBER", "")
    if cur_number == PLACEHOLDER_NUMBER:
        cur_number = ""

    if args.sim:
        token = (args.token or cur_token).strip()
        number = normalize_number(args.numero or cur_number)
        agenda = (args.agenda if args.agenda is not None else current.get("CALENDLY_URL", "")).strip()
        empresa = (args.empresa if args.empresa is not None else current.get("MSG_EMPRESA", "")).strip()
        if not token or not number:
            print("ERRO: no modo --sim, informe --token e --numero.")
            return 1
    else:
        print()
        print("Vou fazer 4 perguntas. Para manter o valor entre [colchetes], só aperte Enter.")
        print()
        print("1) API Key da Zappfy")
        print("   É o código do campo \"API Key\", no cartão do seu número em")
        print("   https://dash.zappfy.io/dashboard")
        token = ask("   Cole a API Key aqui", default=cur_token, required=True)
        print()
        print("2) Seu número de WhatsApp")
        print("   É pra ele que o agente manda as mensagens de teste.")
        number = ask("   Digite com DDD (ex.: 11 99999-8888)", default=cur_number,
                     required=True, validate=normalize_number)
        print()
        print("3) Link da sua agenda (Calendly ou parecido) — opcional")
        print("   O agente envia esse link quando o cliente quer marcar um horário.")
        agenda = ask("   Cole o link, ou só aperte Enter se não usa",
                     default="" if "seu-usuario" in current.get("CALENDLY_URL", "") else current.get("CALENDLY_URL", ""))
        print()
        print("4) Nome da sua empresa — opcional")
        print("   Entra na resposta automática de saudação (\"Sou da ...\").")
        empresa = ask("   Digite o nome, ou só aperte Enter pra pular", default=current.get("MSG_EMPRESA", ""))

    set_env_values({
        "ZAPPFY_TOKEN": token,
        "TEST_NUMBER": number,
        "CALENDLY_URL": agenda,
        "MSG_EMPRESA": empresa,
    })
    print()
    print("Configuração salva.")

    print()
    print("-" * 62)
    print(" Testando a conexão com o seu WhatsApp...")
    print("-" * 62)
    if not run_script("health_check.py"):
        print()
        print("A conexão NÃO funcionou. O que conferir:")
        print("  - a API Key foi colada inteira, sem espaço sobrando;")
        print("  - no painel da Zappfy, o seu número aparece como conectado.")
        print("Depois de ajustar, rode de novo:  python3 instalar.py")
        return 1

    print()
    print("-" * 62)
    print(" Puxando a lista dos seus grupos...")
    print("-" * 62)
    run_script("disparo.py", "listar", "--csv-out", "grupos.csv")

    print()
    if args.sim or ask("Instalar o agente no Claude Code? (s/n)", default="s").lower().startswith("s"):
        path = install_agent()
        print(f"Agente instalado em: {path}")
        print("Feche o Claude Code e abra de novo dentro desta pasta.")

    print()
    print("=" * 62)
    print(" Pronto! O agente está instalado.")
    print("=" * 62)
    print("Próximo passo, no Claude Code aberto nesta pasta, peça por exemplo:")
    print("   lista grupos")
    print()
    print("Antes de usar o follow-up automático: os textos prontos têm lacunas")
    print("([TEMA], [PRODUTO]...). Preencha as linhas MSG_ do arquivo .env —")
    print("enquanto estiverem vazias, o agente não envia essas mensagens.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
