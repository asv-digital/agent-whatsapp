# Agent-Whatsapp

> ⚠️ **IMPORTANTE: Leia esta página inteira, é essencial para que você consiga extrair 100% do agent.**

## CRM operacional via WhatsApp

**21 capacidades em 4 camadas** — não é disparador, é canal comercial inteiro.

### O que você precisa ter

São três coisas. As duas primeiras são obrigatórias:

1. **Uma API de WhatsApp.** É o serviço que liga o seu número de WhatsApp ao agente. Logo abaixo explicamos qual usar e como contratar.

2. **Python instalado no computador.** Python é um programa gratuito, e é ele que faz o agente funcionar. Você não precisa saber usar, só precisa ter instalado. Muitos computadores já têm. Para conferir:
   - Abra o **Terminal** (o aplicativo de comandos que já vem no computador).
   - Digite `python3 --version` e aperte Enter.
   - Se aparecer algo como `Python 3.12`, está pronto. Qualquer número a partir de 3.8 serve.
   - Se não aparecer, instale de graça: no Windows, procure por "Python" na Microsoft Store e clique em Instalar; no Mac, aceite a instalação que o próprio computador oferece ou baixe em [python.org/downloads](https://www.python.org/downloads/).

3. **Claude Code (opcional).** É o aplicativo do Claude que roda no seu computador. Com ele você comanda o agente escrevendo em português, em vez de digitar comandos.

### Antes de instalar: você precisa de uma API de WhatsApp

Para o agente ler e enviar mensagens pelo seu WhatsApp, é obrigatório ter uma **API de WhatsApp**. É ela que faz a ponte entre o seu número e o agente — sem API, não tem como conectar.

Por isso recomendamos a que nós usamos: a **[Zappfy](https://zappfy.io)**. O agente já vem pronto para ela.

Como contratar e pegar a sua chave:

1. Entre em [zappfy.io](https://zappfy.io) e clique em **Cadastre-se** para criar a sua conta.
2. Dentro do painel, clique em **Planos** e escolha o plano de **1 número** (um número de WhatsApp já é suficiente).
3. Clique em **Dashboard** e depois em **Nova Instância** ("instância" é o nome que a Zappfy dá para um número conectado).
4. Gere o QR Code e escaneie com o WhatsApp do celular, igual você faz para entrar no WhatsApp Web: **Configurações → Dispositivos conectados → Conectar dispositivo**.
5. Quando conectar, aparece um cartão com o seu número. Copie o código do campo **API Key** — é a chave que você vai colar no passo 4 da instalação.

> Guarde a API Key só com você: quem tem essa chave consegue usar o seu WhatsApp.

### Como instalar (5 minutos)

Os passos abaixo usam o **Terminal**. Não precisa saber programar: é só copiar o comando, colar e apertar Enter.

1. **Baixe o agente.** Clique aqui para baixar: **[⬇️ Agent-Whatsapp.zip](https://github.com/asv-digital/agent-whatsapp/releases/download/v1.0.0/Agent-Whatsapp.zip)**

2. **Descompacte.** Dê dois cliques no arquivo baixado. Vai aparecer uma pasta com os arquivos do agente.

3. **Abra o Terminal dentro dessa pasta.** Abra o aplicativo **Terminal**, digite `cd ` (com um espaço depois), arraste a pasta para dentro da janela e aperte Enter.

4. **Rode o assistente de instalação.** Copie o comando abaixo, cole no Terminal e aperte Enter:

   ```
   python3 instalar.py
   ```

   Ele faz 4 perguntas, e você responde digitando e apertando Enter:

   - **API Key da Zappfy** — a chave que você copiou lá em cima.
   - **Seu número de WhatsApp**, com DDD — é para ele que o agente manda as mensagens de teste.
   - **Link da sua agenda** (Calendly ou parecido) — o agente envia quando o cliente quer marcar horário. Se não usa, só aperte Enter.
   - **Nome da sua empresa** — entra na resposta automática de saudação. Se preferir, só aperte Enter.

   Com as respostas, o assistente testa a conexão com o seu WhatsApp, puxa a lista dos seus grupos e instala o agente no Claude Code, tudo sozinho. Quando aparecer **"Pronto! O agente está instalado"**, deu certo.

   - Se o computador disser que não encontrou `python3`, use `python instalar.py`.
   - Se aparecer que a conexão não funcionou, confira se a API Key foi colada inteira e se o seu número continua conectado no painel da Zappfy. Depois rode o comando de novo.

5. **Use pelo Claude Code, conversando em português.** Feche o Claude Code e abra de novo dentro dessa pasta. A partir daí é só pedir, por exemplo: `lista grupos`.

6. **Antes de usar o follow-up automático, complete os seus textos.** As mensagens prontas de follow-up têm lacunas para você preencher com os dados do seu negócio (o assunto, o produto, o link). No Claude Code, peça: `preenche as lacunas dos meus textos de follow-up`. Ele pergunta cada informação e salva. Enquanto uma lacuna estiver vazia, o agente não envia a mensagem que depende dela.

Mais detalhes e solução de problemas em `COMO-INSTALAR.md`, dentro do zip. A lista de todos os comandos está em [REFERENCIA.md](REFERENCIA.md).

### O que entrega

**Disparo (enviar mensagens)**

- Lista os grupos do seu WhatsApp e mostra em quais você é administrador
- Extrai os contatos dos grupos para uma planilha, sem números repetidos
- Filtra esses contatos (por DDD, por quem é administrador, por quantos grupos participa)
- Envia a mesma mensagem para vários grupos de uma vez
- Envia mensagem individual, uma a uma, chamando cada pessoa pelo nome
- Agenda o envio para o dia e a hora que você escolher
- Testa duas versões da mensagem e mostra qual teve mais resposta

**Inbox (respostas que chegam)**

- Lê as mensagens que chegam no seu WhatsApp
- Classifica cada resposta em 8 tipos: pediu para sair, quer agendar, interessado, objeção, sem interesse, pergunta, saudação e ruído
- Age sozinho conforme o tipo: tira da lista quem pediu para sair, manda o link da agenda para quem quer agendar e avisa você quando alguém demonstra interesse

**CRM (organização dos contatos)**

- Guarda o histórico de cada contato num banco de dados que fica no seu computador
- Qualifica cada lead com 3 perguntas e dá uma nota de 0 a 100
- Faz follow-up automático por até 30 dias, com 4 sequências prontas: follow-up padrão, recuperação de carrinho, pós-proposta e reativação
- Organiza os contatos num funil de vendas com 7 etapas
- Calcula a previsão de receita
- Mostra a taxa de conversão entre as etapas do funil
- Aponta os leads que ficaram sem contato

**Contexto (personalização)**

- Cruza seus contatos com planilhas (CSV ou Google Sheets) para personalizar as mensagens com os dados de cada pessoa
- Lê uma página da internet para usar o conteúdo dela na mensagem
- Gera um relatório de cada disparo

### Suporte

WhatsApp: [+55 21 97532-8361](https://wa.me/5521975328361)

### Licença

Uso permitido para clientes ASV Digital / Bravy. Não redistribuir.
