# 💬 Assistente do WhatsApp

Um programa que fica quietinho no seu computador e faz duas coisas:

1. 🌅 Manda as mensagens que você escrever, nas conversas e horários que você
   escolher — já vem pronto com **"Bom dia!"** e **"Boa noite!"**, e você pode
   acrescentar quantas quiser.
2. 🔔 Toca um aviso do Windows quando alguém **te menciona** (`@SeuNome`) em um
   grupo.

🔓 Não precisa de senha de Administrador em nenhum momento.

---

## 🚀 Instalação (uma vez só)

**Passo 1 — ⚙️ Instalar.** Clique duas vezes em **`setup.bat`** e espere a
mensagem "Instalação concluída". Se aparecer um aviso dizendo que o Python não
foi encontrado, a própria janela explica o que baixar.

**Passo 2 — ✏️ Configurar.** Abra o arquivo **`config.json`** (botão direito →
*Abrir com* → *Bloco de Notas*) e preencha:

O arquivo já vem com textos de exemplo em MAIÚSCULAS. Troque **apenas o que
está entre aspas**, mantendo as aspas, as vírgulas e as chaves no lugar:

```json
{
  "user_name": "SEU NOME AQUI",
  "target_chats": [
    "NOME EXATO DA PRIMEIRA CONVERSA",
    "NOME EXATO DA SEGUNDA CONVERSA"
  ],
  "scheduled_messages": [
    { "name": "bom dia", "time": "08:30", "message": "Bom dia!" },
    { "name": "boa noite", "time": "21:30", "message": "Boa noite!" }
  ],
  "check_interval_seconds": 12,
  "browser_channel": "msedge"
}
```

✅ Ficaria assim depois de preencher:

```json
{
  "user_name": "Maria Silva",
  "target_chats": ["Família ❤️", "Amigas do trabalho"],
  "scheduled_messages": [
    { "name": "bom dia", "time": "07:00", "message": "Bom dia!" },
    { "name": "boa noite", "time": "22:00", "message": "Boa noite!" }
  ],
  "check_interval_seconds": 12,
  "browser_channel": "msedge"
}
```

Pode ter uma conversa só, ou cinco — basta separar por vírgula. Enquanto os
textos de exemplo não forem trocados, o programa avisa e não faz nada.

| Campo | O que colocar |
| --- | --- |
| 🙋 `user_name` | Seu nome como ele aparece quando alguém te menciona no grupo. |
| 💬 `target_chats` | Nomes das conversas/grupos, **exatamente** como aparecem na sua lista do WhatsApp. |
| ⏰ `scheduled_messages` | A lista de mensagens automáticas (veja abaixo). |
| 🔁 `check_interval_seconds` | De quantos em quantos segundos procurar menções. Deixe `12`. |
| 🌐 `browser_channel` | `"msedge"` para o Microsoft Edge ou `"chrome"` para o Google Chrome. |

### ✍️ Criando suas próprias mensagens

Cada item de `scheduled_messages` fica entre `{ }` e aceita:

| Campo | Obrigatório | O que faz |
| --- | --- | --- |
| ⏰ `time` | ✅ sim | Horário, 24 horas: `"07:00"`, `"13:30"`, `"22:45"`. |
| 💬 `message` | ✅ sim | O texto que será enviado. |
| 🏷️ `name` | ➖ não | Apelido usado nos avisos e em `--send-now`. Sem ele, vale o próprio texto. |
| 👥 `chats` | ➖ não | Conversas só dessa mensagem. Sem ele, usa `target_chats`. |
| 📅 `days` | ➖ não | Dias em que ela vale. Sem ele, todos os dias. |
| 🔀 `enabled` | ➖ não | `false` desliga a mensagem sem apagar. |

📅 Em `days` você pode escrever os dias (`"segunda"`, `"terça"`, ... `"domingo"`,
com ou sem acento, ou abreviados: `"seg"`, `"ter"`) ou usar um atalho:
`"todos"`, `"dias úteis"`, `"fim de semana"`.

Exemplo com cinco mensagens diferentes:

```json
  "scheduled_messages": [
    { "name": "bom dia", "time": "07:00", "message": "Bom dia! ☀️" },
    { "name": "boa noite", "time": "22:00", "message": "Boa noite! 🌙" },
    {
      "name": "remédio",
      "time": "12:30",
      "message": "Hora do remédio! 💊",
      "chats": ["Mãe"],
      "days": "dias úteis"
    },
    {
      "name": "reunião",
      "time": "08:45",
      "message": "Bom dia, pessoal! Reunião hoje às 9h. 📅",
      "chats": ["Equipe"],
      "days": ["segunda"]
    },
    {
      "name": "domingo",
      "time": "10:00",
      "message": "Bom domingo, família! ❤️",
      "days": ["domingo"],
      "enabled": false
    }
  ]
```

> ℹ️ Se você já usava a versão anterior, com `morning_time` e `evening_time`, o
> arquivo continua funcionando: o programa converte para o formato novo sozinho.

> ⚠️ O nome em `target_chats` precisa ser igual ao do WhatsApp, incluindo
> acentos e maiúsculas. Se o grupo se chama "Família ❤️", escreva
> `"Família ❤️"`. Quando o nome não bate, o programa **não** envia nada e
> registra o aviso — ele nunca manda mensagem para a conversa errada.

**Passo 3 — ▶️ Ligar.** Clique duas vezes em **`start.bat`**. Na primeira vez,
uma janela do navegador vai abrir com um **QR code** 📱: no celular, abra o
WhatsApp → *Dispositivos conectados* → *Conectar dispositivo* e aponte para a
tela. Pronto — isso é feito uma única vez.

🎉 Das próximas vezes o programa abre sozinho, sem janela nenhuma aparecendo.

---

## 🔄 Deixar ligado sempre (opcional)

Para o assistente iniciar junto com o Windows:

1. ⌨️ Aperte `Windows + R`, digite `shell:startup` e dê *Enter*. Uma pasta abre.
2. 📋 Clique com o botão direito em **`start.bat`** → *Copiar*.
3. 📎 Dentro da pasta que abriu, clique com o botão direito → *Colar atalho*.

🗑️ Para desfazer, apague o atalho dessa pasta.

## 🛑 Como desligar o programa

Ele roda invisível, então:

1. ⌨️ Aperte `Ctrl + Shift + Esc` (Gerenciador de Tarefas).
2. 🔍 Procure **`pythonw.exe`** na lista.
3. ❌ Clique nele e depois em *Finalizar tarefa*.

---

## 🩺 Quando algo não funciona

📝 Tudo que o programa faz fica anotado em um arquivo de texto. Para abrir:
aperte `Windows + R`, cole o caminho abaixo e dê *Enter*.

```
%LOCALAPPDATA%\WhatsAppBotData\assistente.log
```

| Sintoma | O que fazer |
| --- | --- |
| 🔕 Não recebi o aviso de menção | Confira se `user_name` é o nome que aparece no `@`. Teste as notificações: veja "Comandos extras" abaixo. |
| 📭 A mensagem não foi enviada | O nome da conversa provavelmente está diferente do WhatsApp. Rode o diagnóstico (abaixo): ele diz exatamente qual nome não foi encontrado. |
| 📱 Pediu o QR code de novo | Normal de vez em quando: o WhatsApp desconecta aparelhos antigos. A janela abre sozinha para você escanear. |
| 🚫 Nada acontece ao clicar em `start.bat` | Rode `setup.bat` novamente e veja se aparece algum erro em vermelho. |
| 🪟 A janela do WhatsApp aparece cortada ou minúscula | Rode `--diagnose`: a linha `Janela` mostra o tamanho da janela, da página e da tela. Se aparecer um `AVISO` ali, mande essa linha junto com o pedido de ajuda. |
| 👯 Abri duas vezes sem querer | Sem problema: o programa detecta e não abre uma segunda cópia. |

### 🧰 Comandos extras

Para quem estiver ajudando com o computador — abra o Prompt de Comando na
pasta do projeto:

```bat
.venv\Scripts\python.exe main.py --diagnose
.venv\Scripts\python.exe main.py --test-notification
.venv\Scripts\python.exe main.py --send-now "bom dia"
.venv\Scripts\python.exe main.py --verbose
start.bat --visible                REM mostra a janela do navegador
```

⚠️ Use `python.exe` (e **não** `pythonw.exe`) nesses comandos, senão as
mensagens não aparecem na tela.

🩺 **`--diagnose`** é o primeiro a rodar quando algo para de funcionar. Ele não
envia nada e responde:

- 🔌 a sessão do WhatsApp está de pé, ou precisa de QR code?
- 🎯 cada conversa do `config.json` foi encontrada na busca? (`OK` / `FALHA`)
- 🧩 qual seletor casou com cada elemento da página — se algum aparecer como
  `(nenhum)`, o WhatsApp mudou o HTML e o conserto é em
  `src/whatsapp_driver.py`;
- 👀 o que o programa está enxergando na lista de conversas agora.

📤 **`--send-now "bom dia"`** envia aquela mensagem na hora, útil para confirmar
os nomes das conversas. O nome é o campo `name` do `config.json`; se você errar,
o programa lista os nomes disponíveis.

---

## 🔒 Sobre a segurança da sua conta

Este programa controla o **WhatsApp Web** como se fosse você mexendo no mouse e
no teclado. Ele foi feito para chamar o mínimo de atenção possível:

- 🖥️ usa o Edge/Chrome de verdade que já está instalado, com a sua sessão salva;
- ⌨️ digita **letra por letra**, com pausas diferentes a cada tecla;
- ⏳ espera alguns segundos antes de clicar e antes de enviar;
- 🎲 envia as mensagens com um **atraso aleatório** (10 a 300 segundos) sobre o
  horário marcado, para nunca cair no mesmo segundo todo dia;
- 🧍 espaça as conversas entre 8 e 25 segundos;
- 👀 só olha a lista de conversas a cada 12 segundos, sem recarregar a página.

⚠️ Ainda assim, vale saber: automatizar o WhatsApp contra os Termos de Uso da
Meta — eles não oferecem uma forma oficial de fazer isso em contas pessoais. O
risco de bloqueio é baixo com o comportamento acima, mas **não é zero**.
Mantenha poucas conversas na lista, horários normais, e não aumente a
frequência.

---

## 🧑‍💻 Parte técnica

### 📁 Estrutura

```
/
├── config.json          Configuração do usuário
├── requirements.txt     Dependências
├── requirements-dev.txt Dependências só dos testes
├── setup.bat            Cria .venv, instala tudo (espaço de usuário)
├── start.bat            Roda com pythonw.exe (sem console)
├── main.py              Loop principal, logging, trava de instância única
├── src/
│   ├── config.py            Leitura + validação do config.json
│   ├── whatsapp_driver.py   Playwright: sessão, envio, varredura de menções
│   ├── scheduler.py         Horários + jitter, estado em disco
│   └── notifier.py          Toasts nativos do Windows
└── tests/
    ├── fake_whatsapp.html   Página local que imita o WhatsApp Web
    ├── test_config.py       Validação do config.json
    ├── test_scheduler.py    Horários, jitter, dias, não-duplicar
    ├── test_mentions.py     Detecção de @menções
    └── test_driver_dom.py   Playwright real contra a página falsa
```

### 🧪 Rodando os testes

```bash
pip install -r requirements-dev.txt
pytest                  # tudo (116 testes)
pytest -m "not dom"     # só a lógica, sem navegador (rápido)
```

🌐 Os testes marcados com `dom` sobem um Chromium de verdade contra
`tests/fake_whatsapp.html` e são pulados automaticamente onde não há navegador
ou display. A página falsa reproduz só o que o driver toca — e uma das linhas
dela ("Ciladas") abre um cabeçalho com **outro** nome, provando que o driver se
recusa a digitar na conversa errada.

### 💡 Decisões que valem explicação

🧵 **Um único thread.** A API síncrona do Playwright não pode ser chamada de
outra thread. Por isso o agendamento usa `schedule` (que executa os jobs no
thread de quem chama `run_pending()`) e não `APScheduler` com
`BackgroundScheduler`, cujos executores rodam em threads separadas e quebrariam
o driver.

🎲 **Jitter sem bloquear.** No horário configurado o job é apenas *armado* com um
alvo aleatório; o `tick()` seguinte que passar desse alvo é o que envia. Assim o
atraso de até 5 minutos não paralisa a detecção de menções.

🕵️ **Nunca headless.** O WhatsApp Web funciona mal sem interface e o fingerprint
headless é trivial de detectar. Para "rodar em segundo plano", a janela é
posicionada fora da área visível (em `-32000,-32000`, a mesma posição que o
Windows usa para janelas minimizadas) — navegador real, janela invisível.

🪟 **A janela é medida pela tela, nunca fixada.** Passar
`viewport={"width": 1920, "height": 1080}` parece inofensivo, mas em modo headful
o Playwright honra o viewport **redimensionando a janela** para que a área de
conteúdo tenha exatamente aquela medida — o que dá uma janela de ~1928x1211. Numa
tela 1920x1080, ou em qualquer tela com escalonamento do Windows a 125%/150%
(onde a área útil lógica é bem menor), essa janela não cabe: sobra fora do
monitor e o Chrome comprime a página no que restou. O sintoma é a interface
minúscula e embolada, com a caixa de mensagem abaixo da borda da tela.

A correção é `no_viewport=True` (a página passa a usar o tamanho real da janela)
mais `Browser.setWindowBounds` via CDP, calculado a partir de
`screen.availWidth/availHeight`. Tamanho e posição **não** vão por
`--window-size`/`--window-position`: alguns gerenciadores de janela ignoram essas
flags, e o Chrome ainda restaura por cima delas o tamanho salvo no perfil. Como o
ajuste roda em todo lançamento (antes de carregar o WhatsApp, senão o layout sai
torto), ele também conserta perfis que já ficaram com a medida ruim.

🪪 **User-Agent não é sobrescrito** quando usamos Edge/Chrome de verdade: um UA
inventado que não combina com o navegador é *mais* detectável que o verdadeiro.
O UA fixo só entra no fallback para o Chromium do Playwright.

✅ **Confirmação antes de digitar.** `open_chat()` compara o título no cabeçalho
da conversa aberta com o nome pedido e devolve `None` se forem diferentes — nada
é digitado. Depois do envio, `send_message()` confere a última bolha enviada; se
não confirmar, registra o aviso e **não** reenvia (duplicar é pior que um log).

🔍 **Busca sempre limpa.** Depois de abrir uma conversa, o campo de busca é
esvaziado. Um filtro esquecido ali faria `scan_chat_list()` ler só as linhas que
casam com aquele texto, e menções de outras conversas passariam batido. O Escape
normalmente limpa; não dependemos disso.

🗓️ **Agendamento genérico.** O scheduler não conhece "manhã" nem "noite":
percorre a lista de `ScheduledMessage` vinda da configuração, cada uma com
horário, texto, conversas e dias da semana próprios. O filtro de dia da semana é
aplicado na hora de disparar (o `schedule` arma todos os dias).

💾 **Estado em disco.** `state.json` guarda a data do último envio de cada
mensagem (pela chave dela), e a marcação acontece *antes* do envio. Reiniciar o
programa no meio do dia nunca duplica um "Bom dia!". Configs no formato antigo
mantêm as chaves `morning`/`evening`, então o `state.json` existente continua
valendo.

⏱️ **Janela de atraso.** Se o computador estava suspenso e o horário passou há
mais de 90 minutos (`max_catch_up_minutes`), a mensagem é descartada — ninguém
manda "Bom dia!" às três da tarde.

🔐 **Trava de instância única.** Dois processos no mesmo perfil do Chrome
corrompem a sessão. `assistente.lock` guarda o PID e é limpo automaticamente se
o processo anterior morreu.

### 🎯 Seletores do WhatsApp Web

O WhatsApp muda o HTML sem aviso. Todos os seletores estão agrupados no topo de
`src/whatsapp_driver.py`, cada alvo com uma lista de candidatos (do mais estável
para o mais específico); o código usa o primeiro que estiver visível. Se um dia o
envio parar de funcionar, é o único lugar a ajustar — e `--diagnose` diz qual
candidato deixou de casar.

### 🎛️ Opções não obrigatórias do `config.json`

| Campo | Padrão | Efeito |
| --- | --- | --- |
| 🏷️ `mention_names` | `[]` | Apelidos extras que contam como menção. |
| 🎲 `jitter_min_seconds` / `jitter_max_seconds` | `10` / `300` | Faixa do atraso aleatório. |
| ⏱️ `max_catch_up_minutes` | `90` | Atraso máximo tolerado para ainda enviar. |
| 👁️ `hide_window` | `true` | `false` mantém a janela do navegador visível. |
| 🌍 `locale` | `"pt-BR"` | Idioma do navegador. |

### 💾 Dados em disco

Tudo em `%LOCALAPPDATA%\WhatsAppBotData\` (nenhuma escrita fora do espaço do
usuário):

```
browser_profile\    sessão do WhatsApp (cookies, localStorage)
assistente.log      log rotativo, 4 arquivos de 1 MB
state.json          data do último envio de cada saudação
assistente.lock     trava de instância única
```

🔑 `browser_profile` contém a sessão autenticada da sua conta — trate como senha
e não copie para outro computador.
