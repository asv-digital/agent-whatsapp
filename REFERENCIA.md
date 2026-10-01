# Agent-Whatsapp — referência técnica

> Este arquivo é a referência de todos os comandos. Para instalar, siga o `README.md`.

**CRM operacional via WhatsApp.** Não é disparador. É o canal comercial inteiro: dispara, recebe, classifica intenção, qualifica BANT, dispara follow-up multi-toque, gerencia pipeline 7 estágios, calcula forecast ponderado, audita abandonados, enriquece com contexto externo, gera relatório executivo. Tudo persistido em SQLite local — auditável 6 meses depois.

> Produto **Bravy / ASV Digital** — uso comercial liberado pra clientes ASV.

## Para quem é

Negócio que vende mid/high ticket via WhatsApp e hoje:

- Não vê quem respondeu o quê (perde lead quente porque ninguém viu)
- Esquece de fazer follow-up no D+3 e D+7 (lead esfria)
- Dispara pra quem já pediu opt-out (multa LGPD na esquina)
- Não consegue dizer pro sócio quanto vai faturar mês que vem só com WhatsApp

Custo de ferramentas equivalentes (HubSpot + ManyChat + Calendly + cadência): R$ 800-2.500/mês. Este pacote: comprou uma vez, roda no seu Mac/VPS.

## 21 capacidades em 4 camadas

### Camada 1 — DISPARO

| # | Capacidade | Comando |
|---|---|---|
| 1 | Listar grupos com role admin/membro | `disparo.py listar` |
| 2 | Extrair leads (E.164 + dedup + hash SHA-256) | `extrair_leads.py --dedup` |
| 3 | Segmentar leads (DDD/grupos/admin/blacklist) | `segmentar_leads.py` |
| 4 | Broadcast em GRUPOS com mention + jitter + retry | `disparo.py broadcast` |
| 5 | X1 (1:1) personalizado com `{{placeholders}}` | `disparo.py x1` |
| 6 | Agendamento (broadcast OU x1) | `disparo.py agendar` |
| 7 | A/B test com z-test 95% | `ab_test.py split + apurar` |

### Camada 2 — INBOX (NOVA)

| # | Capacidade | Comando |
|---|---|---|
| 8 | Pull/watch de mensagens recebidas | `inbox.py pull` / `watch` |
| 9 | Webhook HTTP pra Zappfy (tempo real) | `inbox.py webhook --port 8765` |
| 10 | Classificador 8 intents (sem ML, regex+heurística) | `intent.py` (auto) |
| 11 | Auto-triage: opt_out→blacklist, agendamento→Calendly, interessado→SQL | `inbox.py triage` |

### Camada 3 — CRM (NOVA)

| # | Capacidade | Comando |
|---|---|---|
| 12 | Banco SQLite local (7 tabelas) | `db.py` (auto) |
| 13 | Qualificação BANT/SPIN comprimida em 3 perguntas | `qualificar.py start + parse` |
| 14 | Cadência multi-toque D+0..D+30 (4 built-in + custom) | `followup.py enroll + fire-once` |
| 15 | Pipeline kanban 7 estágios | `pipeline.py show` |
| 16 | Forecast ponderado por probabilidade × ticket | `pipeline.py forecast` |
| 17 | Funil com taxa de conversão estágio a estágio | `pipeline.py funnel` |
| 18 | Audit de leads abandonados (sem touch há N dias) | `pipeline.py audit` |

### Camada 4 — CONTEXTO (NOVA)

| # | Capacidade | Comando |
|---|---|---|
| 19 | Enrich CSV/Google Sheets/JSON cruzando por phone | `contexto.py enrich` |
| 20 | Fetch HTML (página web) → contexto pro lead | `contexto.py fetch-html` |
| 21 | Relatório executivo (single OU semanal) | `relatorio.py --log / --week` |

## Fluxo do dia comercial — exemplo real

```
> health
🟢 API: 200 OK · latência 320ms
🟢 Grupos visíveis: 18
🟢 Erro 24h: 0.8%

> pull inbox && triage
✅ pull: 12 novas · 3 duplicadas · 15 da API
triage: 12 mensagens
  3  promovido_sql
  2  blacklist
  4  saudacao_respondida
  1  agendamento + link enviado
  2  ignored_ruido

> kanban
▮ NOVO (8) ▮ MQL (12) ▮ SQL (5)  ← os 3 quentes de hoje
▮ EM_CONVERSA (7) ▮ PROPOSTA (3) ▮ NEGOCIACAO (2)

> forecast --ticket 2500
PROJEÇÃO PONDERADA  R$ 18.750
GANHO REALIZADO     R$ 25.000  (10 fechamentos)

> audit --days 7
[15 leads sem toque há ≥7d, ordenados por fit]

> enroll-csv leads_audit.csv em followup_padrao
✅ 15 enrolled — 75 jobs criados

> fire followup
✅ 12 disparados | 2 skipped (responderam) | 1 falha
```

## Pré-requisitos

- **API de WhatsApp — [Zappfy](https://zappfy.io)** com instância conectada e a **API Key** dela. É a API que a Bravy usa e recomenda; como contratar está logo abaixo.
- **Python 3.8+** (zero dependências externas — só stdlib).
- **Claude Code** logado (recomendado, mas opcional — todos comandos rodam via CLI puro).
- (Opcional) Conta **Calendly** ou Cal.com pra agendamento automático.

### Ainda não tem a API do WhatsApp?

Pra ligar o WhatsApp ao Claude você precisa de uma API de WhatsApp. Este agente roda em cima da **[Zappfy](https://zappfy.io)**:

1. Cadastre-se em <https://zappfy.io> e escolha o plano pela quantidade de números (1 número = 1 instância; 1 já basta).
2. No painel, **Dashboard → Nova Instância** e escaneie o QR Code com o seu WhatsApp.
3. Copie a **API Key** do card da instância e cole em `ZAPPFY_TOKEN` no `.env`.

Passo a passo completo em `COMO-INSTALAR.md` (seção 1.1).

## Instalação rápida

Descompacte o `Agent-Whatsapp.zip`, abra o Terminal dentro da pasta e rode o assistente:

```bash
python3 instalar.py
```

Ele faz 4 perguntas (API Key da Zappfy, seu número, link da agenda e nome da empresa), cria o arquivo de configuração (`.env`), testa a conexão, puxa a lista dos seus grupos e instala o agente no Claude Code. Funciona igual no Mac, Windows e Linux. Se o comando `python3` não existir no seu computador, use `python instalar.py`.

Depois, feche o Claude Code e abra de novo dentro da pasta.

**Antes de usar o follow-up automático:** os textos prontos têm lacunas (`[TEMA]`, `[PRODUTO]`...). Preencha as linhas `MSG_` do `.env`. Enquanto estiverem vazias, o agente não envia as mensagens que dependem delas.

<details>
<summary>Instalação manual (sem o assistente)</summary>

```bash
# 1. Configurar: copie .env.example com o nome .env e preencha ZAPPFY_TOKEN e TEST_NUMBER
cp .env.example .env

# 2. Health-check
python3 health_check.py

# 3. Listar grupos da instância
python3 disparo.py listar --csv-out grupos.csv

# 4. Instalar agente Claude Code
mkdir -p .claude/agents && cp agent-whatsapp.md .claude/agents/
# /exit + reabra Claude Code
```

</details>

## Uso via Claude Code (linguagem natural)

```
> lista grupos
> extrai leads dedup
> dispara: estamos AO VIVO! 🔥 entra 👇 https://link.com
> x1: oi {{first_name}}, vi a {{empresa}}, separei isso pra você
> pull inbox && triage
> kanban
> forecast
> qualifica 5511999990001 nome=Maria
> enroll-csv leads_quentes.csv em pos_proposta
> fire followup
> enrich leads de https://docs.google.com/.../export?format=csv chave=phone
> fetch contexto 5511999990001 https://empresa-do-lead.com.br
> relatório semana
```

## Arquitetura técnica

- **13 scripts Python** sem dependências externas (stdlib pura: `urllib`, `sqlite3`, `csv`, `json`, `re`, `argparse`, `http.server`).
- **SQLite local** (`data.db`) com 7 tabelas: `leads`, `touches`, `inbox`, `conversations`, `followup_jobs`, `pipeline_events`, `campaigns`. Schema versionado.
- **Camada de proteção**: jitter ±20%, retry 3x backoff exponencial, blacklist persistente cross-canal, dedup cross-grupo, health-check obrigatório >50 destinos, validação E.164 BR com DDDs válidos, hash SHA-256 dos números.
- **Logs estruturados** (`timestamp|number|kind|status|err`) auditáveis pra LGPD.
- **Claude Code agent** que mapeia linguagem natural → comando CLI exato.

## Limites operacionais (decoradinho)

| Métrica | Grupos | X1 (1:1) |
|---|---|---|
| Volume/min | 40-50 | 25-30 |
| Volume/h | 200 | 150 |
| Volume/dia | 1.500-3.000 | 800-1.500 |
| Delay mín | 30s | 45s |
| Delay padrão | 60s ±20% | 75s ±20% |
| `mentionEveryone` | sim | n/a |
| Personalização obrigatória | não | sim (`{{first_name}}` mín) |

## Pipeline com forecast

7 estágios com probabilidade (configurável em `db.py`):

```
novo          5%    →  just enrolled
mql          10%    →  engajou (engagement_score >= 30)
sql          25%    →  fit declarado (intent=interessado OU fit >= 70)
em_conversa  40%    →  respondeu na última semana
proposta     60%    →  proposta enviada
negociacao   75%    →  discutindo termos
ganho       100%
perdido       0%
```

`forecast = Σ (leads_no_estagio × probabilidade × ticket_medio)`

## Cadências built-in

```
followup_padrao        D+0, D+1, D+3, D+7, D+14   (5 toques)
recuperacao_carrinho   D+0, D+1, D+3              (3 toques, urgência)
pos_proposta           D+1, D+3, D+7, D+14, D+30  (5 toques + break-up)
reativacao             D+0, D+7, D+30             (3 toques, base fria)
```

Custom: criar `cadencias/<nome>.csv` com colunas `offset_days,text` (suporta `\n` literal). Operador roda `enroll --cadencia <nome>`.

## Intent classifier — 8 categorias

```
opt_out         → auto-blacklist + responde "ok, parei" + status=perdido
agendamento     → envia CALENDLY_URL + state=aguardando_reuniao
interessado     → status=sql + tag 'quente' + alerta humano 🔥
objecao_preco   → status=em_conversa + tag 'objecao_preco' + alerta 💰
sem_interesse   → status=perdido + tag 'frio'
pergunta        → state=em_conversa + alerta humano ❓
saudacao        → responde com {{first_name}}
ruido           → ignora (kk, ok, emoji só)
```

Sem ML, sem dependência externa — regex + heurística. Self-test: `python3 intent.py`.

## Segurança e LGPD

- **Token nunca no código.** Sempre `.env`. Auditoria automática em scripts.
- **Lista comprada/raspada = recusa** do agente.
- **Hash SHA-256** dos números nos CSVs auditáveis.
- **Opt-out automático** via `opt_out` intent + blacklist persistente cross-canal.
- **Logs estruturados** servem como evidência LGPD em caso de denúncia.
- **`.env`, `data.db`, `grupos.csv`, `contatos.csv`, `blacklist.txt`, `logs/`** todos no `.gitignore`.

## Estrutura de arquivos

```
Agent-Whatsapp/
├── agent-whatsapp.md            # agente Claude Code (21 capacidades)
├── instalar.py                  # assistente de instalação (cria o .env, testa e instala o agente)
├── disparo.py                   # core: listar/preview/teste/broadcast/x1/retry/agendar
├── extrair_leads.py             # exporta + importa + dedup + E.164 + SHA-256
├── segmentar_leads.py           # filtro por DDD/grupos/admin/blacklist/nome
├── ab_test.py                   # split 50/50 + z-test 95%
├── relatorio.py                 # relatório executivo .md
├── health_check.py              # 4 sinais 🟢🟡🔴
├── db.py                        # camada SQLite (7 tabelas + helpers)
├── intent.py                    # classificador 8 intents
├── inbox.py                     # pull/watch/webhook + triage automático
├── qualificar.py                # BANT/SPIN 3Q + scoring 0-100
├── followup.py                  # cadência multi-toque D+0..D+30
├── pipeline.py                  # kanban + forecast + funnel + audit + promote
├── contexto.py                  # enrich CSV/Sheets/JSON + fetch-html
├── blacklist.txt                # opt-out cross-canal
├── grupos.csv.example           # template grupos
├── contatos.csv.example         # template contatos x1
├── .env.example                 # template variáveis
├── .gitignore                   # protege credenciais e dados sensíveis
├── README.md, REFERENCIA.md, COMO-INSTALAR.md, PLAYBOOK-CAMPANHAS.md
└── (runtime: data.db, logs/, scheduled/, campaigns/, cadencias/)
```

## Comparação com soluções de mercado

| Recurso | Disparador caseiro | RD Station / HubSpot | **Agent-Whatsapp** |
|---|:-:|:-:|:-:|
| Disparo grupo + x1 | ✅ | ⚠️ pago | ✅ |
| Personalização placeholder | ❌ | ✅ | ✅ |
| Inbox + classificação | ❌ | ⚠️ pago | ✅ |
| Auto-blacklist por intent | ❌ | ❌ | ✅ |
| Cadência multi-toque | ❌ | ✅ | ✅ |
| Pipeline com forecast | ❌ | ✅ | ✅ |
| Qualificação BANT via WhatsApp | ❌ | ❌ | ✅ |
| Enrich externo (Sheets/JSON) | ❌ | ⚠️ enterprise | ✅ |
| Fetch HTML pra contextualizar | ❌ | ❌ | ✅ |
| Linguagem natural via Claude Code | ❌ | ❌ | ✅ |
| Custo mensal | R$ 0 | R$ 800-2.500 | R$ 0 |
| Self-host / dados próprios | ✅ | ❌ | ✅ |

## Suporte

- WhatsApp: [+55 21 97532-8361](https://wa.me/5521975328361)
- Pacote completo Bravy / ASV Digital — 56+ agentes operacionais.

## Licença

Uso permitido pra clientes ASV Digital / Bravy. Não redistribuir.
