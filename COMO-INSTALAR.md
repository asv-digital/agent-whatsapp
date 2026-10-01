# Como instalar — Agent-Whatsapp

Passo a passo do zero ao primeiro disparo seguro com relatório executivo.

## 1. Pré-requisitos

- **API de WhatsApp (Zappfy)** com instância conectada via QR Code e a **API Key** dela em mãos. Ainda não tem? Siga o passo **1.1** abaixo.
- **Python instalado** (versão 3.8 ou mais nova). É o programa gratuito que faz o agente funcionar. Pra conferir, abra o Terminal e rode `python3 --version`. Se não tiver: no Windows, instale pela Microsoft Store (procure por "Python"); no Mac, aceite a instalação que o computador oferece ou baixe em <https://www.python.org/downloads/>.
- **Claude Code** instalado e logado (opcional, mas recomendado): <https://docs.claude.com/claude-code>.

### 1.1 Contratar a API do WhatsApp (Zappfy) e pegar a API Key

Pra ligar o seu WhatsApp ao Claude você precisa de uma API de WhatsApp. Este agente foi construído em cima da **[Zappfy](https://zappfy.io)** — é a API que a Bravy usa e recomenda. Se já tem instância conectada, pule pro passo 2.

1. Acesse <https://zappfy.io> e clique em **Cadastre-se** (ou direto em <https://dash.zappfy.io/signup>).
2. No painel, abra **Planos** e escolha pela quantidade de números que vai conectar (1, 3 ou 5). **1 número de WhatsApp = 1 instância.** Pra este agente, 1 número já basta.
3. Volte em **Dashboard** e clique em **Nova Instância**.
4. Escaneie o QR Code com o WhatsApp que vai operar: no celular, *Configurações → Dispositivos conectados → Conectar dispositivo*.
5. Quando o card da instância mostrar o status conectado, copie o campo **API Key** (um código no formato `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`). É esse valor que o assistente de instalação pede no passo 3.

> A Zappfy recomenda usar **WhatsApp Business** em vez do WhatsApp comum — o comum pode desconectar e ficar instável com API.

> A API Key dá acesso total ao seu WhatsApp. Não cole em chat, print ou repositório — só no `.env`.

Documentação da Zappfy: <https://docs.zappfy.io>

## 2. Descompactar e abrir

Dê dois cliques no `Agent-Whatsapp.zip` pra descompactar. Depois abra o **Terminal** dentro da pasta que apareceu: abra o aplicativo Terminal, digite `cd ` (com um espaço depois), arraste a pasta pra dentro da janela e aperte Enter.

## 3. Instalar com o assistente (recomendado)

```bash
python3 instalar.py
```

Se o comando `python3` não existir no seu computador, use `python instalar.py`.

O assistente faz 4 perguntas e cuida do resto:

1. **API Key da Zappfy** — o código do campo "API Key" do cartão do seu número (passo 1.1).
2. **Seu número de WhatsApp** — com DDD. É pra ele que o agente manda as mensagens de teste.
3. **Link da sua agenda** (opcional) — Calendly ou parecido. O agente envia quando o cliente quer marcar horário.
4. **Nome da sua empresa** (opcional) — entra na resposta automática de saudação.

Com as respostas, ele cria o arquivo de configuração (`.env`), testa a conexão (passo 4), puxa a lista dos seus grupos (passo 5) e instala o agente no Claude Code (passo 8). Se tudo terminar com "Pronto!", pode pular direto pro passo 6.

### 3.1 Preencha as lacunas dos textos prontos

Os textos prontos de follow-up e de saudação têm lacunas como `[TEMA]` e `[PRODUTO]`. Abra o arquivo `.env` e preencha as linhas que começam com `MSG_`:

| Linha do `.env` | Lacuna que ela preenche | Onde é usada |
|---|---|---|
| `MSG_EMPRESA` | `[SUA EMPRESA]` | resposta automática de saudação |
| `MSG_TEMA` | `[TEMA]` | cadências `followup_padrao` e `pos_proposta` |
| `MSG_BENEFICIO` | `[BENEFÍCIO ESPECÍFICO]` | cadência `followup_padrao` |
| `MSG_PRODUTO` | `[PRODUTO]` | cadência `recuperacao_carrinho` |
| `MSG_NOVIDADE` e `MSG_LINK` | `[NOVIDADE]` e `[LINK]` | cadência `reativacao` |

Enquanto uma linha estiver vazia, o agente **não envia** a mensagem que depende dela — assim nunca chega um `[TEMA]` no WhatsApp do seu cliente.

### 3.2 Configuração manual (se preferir não usar o assistente)

Copie o arquivo `.env.example` com o nome `.env` e preencha:

```
ZAPPFY_TOKEN=cole-aqui-a-api-key-da-sua-instancia
TEST_NUMBER=5511999998888
API_BASE=https://api.zappfy.io
```

Comentário vai sempre em linha própria, começando com `#`.

> ⚠️ NUNCA commite o `.env`. Já está no `.gitignore`.

## 4. Validar instância

```bash
python3 health_check.py
```

Saída esperada:
```
🟢 API: 200 OK · latência <800ms
🟢 Grupos visíveis: N
⚪ Sem disparos nas últimas 24h
⚪ Último broadcast: nenhum
```

Se ver 🔴 em qualquer linha, resolve antes de continuar (token errado, instância desconectada).

## 5. Listar grupos e gerar grupos.csv

```bash
python3 disparo.py listar --csv-out grupos.csv
```

Output:
```
GRUPOS NA INSTÂNCIA (N)
Operador: 5511999998888 (role detectada por participação)
==========================================================================================
  1. Nome do Grupo                         | 12036300...@g.us | 120 | admin
  2. Outro Grupo                           | 12036311...@g.us |  47 | membro
...
Total: N | admin: A | membro: M
CSV: grupos.csv
```

**Edite `grupos.csv` e mantenha SOMENTE os grupos onde você tem permissão de postagem.**

## 6. Configurar blacklist (opcional mas recomendado)

`blacklist.txt` já vem comentado. Adicione números que pediram opt-out (1 por linha, com DDI):

```
5511999990001  # opt-out via WhatsApp 2026-04-22
5521988880002  # cliente cancelou
```

Estes números serão pulados em qualquer extração/disparo futuro.

## 7. Validar fluxo seguro de disparo

```bash
# Cria copy de teste
echo "Teste — favor ignorar." > copy_teste.txt

# Preview (não envia)
python3 disparo.py preview --text-file copy_teste.txt

# Teste no seu WhatsApp pessoal
python3 disparo.py teste --text-file copy_teste.txt

# Se chegou no seu WhatsApp, está tudo OK
```

## 8. Instalar agente Claude Code

O assistente do passo 3 já faz isso. Pra fazer na mão:

### Opção A — só no projeto atual (recomendado)
```bash
mkdir -p .claude/agents
cp agent-whatsapp.md .claude/agents/
```

### Opção B — global
```bash
mkdir -p ~/.claude/agents
cp agent-whatsapp.md ~/.claude/agents/
```

### Reiniciar Claude Code

Saia com `/exit`, abra de novo na pasta. Confirme:
```
/agents
```

Deve aparecer `agent-whatsapp`.

## 9. Primeiro disparo via Claude Code

```
> dispara: aqui vai a copy real com emojis 🔥 e link https://exemplo.com
```

O agente conduz você passo a passo:
1. Mostra preview com lista de grupos.
2. Pergunta `enviar teste no número pessoal? (s/n)` — você responde `s`.
3. Dispara o teste, pergunta `teste OK? (s/n)` — você verifica e responde `s`.
4. Roda broadcast com delay 60s ± 20% jitter, retry 3x.
5. Gera relatório executivo em `./logs/relatorio_<ts>.md`.
6. Devolve UMA linha de resumo com path do log e relatório.

## 10. Disparo agendado pra horário ouro

```
> agenda dispara amanhã 14h: copy do dia D-1
```

O agente:
1. Salva o job em `./scheduled/<id>.json`.
2. Te pede pra rodar o watcher em background:
   ```bash
   python3 disparo.py agenda-watch &
   ```
3. Quando bater a hora, executa automaticamente com fluxo `--confirmed-test` já validado.

## 10b. Disparo X1 (1:1 personalizado)

```bash
# 1. Criar contatos.csv
cp contatos.csv.example contatos.csv
# edite — adicione seus contatos: phone,name (mais colunas opcionais)

# 2. Copy com placeholders
cat > copy_x1.txt <<'EOF'
Oi {{first_name}}, tudo bem?

Tô passando aqui pra avisar que [novidade].

Se não faz sentido pra você, responde SAIR que eu paro.
EOF

# 3. Teste no seu próprio número
python3 disparo.py teste --text-file copy_x1.txt

# 4. Dispara x1 (delay 75s recomendado)
python3 disparo.py x1 --contatos contatos.csv --text-file copy_x1.txt \
  --confirmed-test --delay 75 --jitter 0.2 --retry 3
```

Ou via Claude Code:
```
> x1: oi {{first_name}}, tudo bem? aqui é a [seu nome]...
```

Placeholders disponíveis: `{{name}}`, `{{first_name}}`, `{{phone}}`, e qualquer coluna que você tiver no CSV (`{{tag}}`, `{{empresa}}`, `{{produto}}`, etc).

## 11. A/B test de copy

```bash
# Salve duas versões
echo "Versão A — direta ao ponto" > copy_a.txt
echo "Versão B — com hook emocional" > copy_b.txt

# Dispara
python3 ab_test.py split --copy-a copy_a.txt --copy-b copy_b.txt --csv grupos.csv --window-hours 4 --confirmed-test

# 4h depois
python3 ab_test.py apurar --campaign ab_20260505_143000
```

Saída:
```
A: 38 respostas em 8 grupos → média 4.75 resp/grupo
B: 14 respostas em 8 grupos → média 1.75 resp/grupo

🏆 winner: A (p=0.012) Δ=3.00 resp/grupo
```

## 12. Extração e segmentação de leads

```bash
# Extrair com dedup (1 número aparece 1x mesmo se está em 5 grupos)
python3 extrair_leads.py --output ./leads_$(date +%Y-%m-%d).csv --dedup

# Segmentar — só admins de DDD 11/21 que estão em 3+ grupos
python3 segmentar_leads.py \
  --input ./leads_$(date +%Y-%m-%d).csv \
  --output ./leads_premium.csv \
  --min-groups 3 --only-admin --ddd 11,21 --exclude-blacklist

# Importar lista externa com validação
python3 extrair_leads.py importar --input lista_externa.csv --merge ./leads_$(date +%Y-%m-%d).csv
```

## 13. Relatório executivo

Após cada broadcast, o agente Claude Code já gera. Manualmente:

```bash
# Disparo único
python3 relatorio.py --log logs/disparo_<ts>.log --output relatorio.md --ticket 497

# Semanal agregado
python3 relatorio.py --week
```

O `.md` traz: resumo executivo, falhas por código HTTP com diagnóstico, qualidade da instância (proxy), ROI estimado se passar `--ticket`.

## Solução de problemas

| Sintoma | Causa | Solução |
|---|---|---|
| `ZAPPFY_TOKEN ausente` | `.env` vazio | Rode `python3 instalar.py`. Sem API Key ainda? Passo 1.1 |
| `ERRO HTTP 401` | Token inválido | Copia de novo a **API Key** no painel Zappfy (Dashboard → card da instância) |
| `ERRO HTTP 429` | Rate limit | Aumenta `--delay` (60→90s) |
| `Bloqueado: rode teste` | Pulou passo 3 | Roda `teste` antes do `broadcast --confirmed-test` |
| `CSV não encontrado` | Faltou `grupos.csv` | `python3 disparo.py listar --csv-out grupos.csv` |
| Health-check 🔴 latência | API lenta | Aguarda 5min e tenta — se persistir, pause |
| Health-check 🔴 erro 24h | Instância banida | Pause 24-72h, reconecta QR no Zappfy |
| A/B sem dados de resposta | Ainda não chegou resposta nos grupos | Aguarda mais tempo e rode `apurar` de novo |
| Follow-up mostra `⏸ falta preencher MSG_...` | Texto pronto com lacuna sem preencher | Preencha a linha `MSG_` indicada no `.env` (passo 3.1) e rode de novo |
| `inbox.py pull` falha em `/chat/find` | Token inválido ou instância desconectada | `python3 health_check.py` e confira o painel da Zappfy |
| Watcher não dispara | Esqueceu de rodar `agenda-watch` | `python3 disparo.py agenda-watch &` |

## Suporte

- WhatsApp: [+55 21 97532-8361](https://wa.me/5521975328361)
