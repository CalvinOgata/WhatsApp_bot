# Assistente do WhatsApp

Um programa que fica quietinho no seu computador e faz três coisas:

1. Manda **"Bom dia!"** nas conversas que você escolher, no horário que você escolher.
2. Manda **"Boa noite!"** do mesmo jeito.
3. Toca um aviso do Windows quando alguém **te menciona** (`@SeuNome`) em um grupo.

Não precisa de senha de Administrador em nenhum momento.

---

## Instalação (uma vez só)

**Passo 1 — Instalar.** Clique duas vezes em **`setup.bat`** e espere a mensagem
"Instalação concluída". Se aparecer um aviso dizendo que o Python não foi
encontrado, a própria janela explica o que baixar.

**Passo 2 — Configurar.** Abra o arquivo **`config.json`** (botão direito →
*Abrir com* → *Bloco de Notas*) e preencha:

O arquivo já vem com textos de exemplo em MAIÚSCULAS. Troque **apenas o que
está entre aspas**, mantendo as aspas, as vírgulas e as chaves no lugar:

```json
{
  "user_name": "SEU NOME AQUI",
  "morning_time": "08:30",
  "evening_time": "21:30",
  "target_chats": [
    "NOME EXATO DA PRIMEIRA CONVERSA",
    "NOME EXATO DA SEGUNDA CONVERSA"
  ],
  "check_interval_seconds": 12,
  "browser_channel": "msedge"
}
```

Ficaria assim depois de preencher:

```json
{
  "user_name": "Maria Silva",
  "morning_time": "07:00",
  "evening_time": "22:00",
  "target_chats": ["Família ❤️", "Amigas do trabalho"],
  "check_interval_seconds": 12,
  "browser_channel": "msedge"
}
```

Pode ter uma conversa só, ou cinco — basta separar por vírgula. Enquanto os
textos de exemplo não forem trocados, o programa avisa e não faz nada.

| Campo | O que colocar |
| --- | --- |
| `user_name` | Seu nome como ele aparece quando alguém te menciona no grupo. |
| `morning_time` | Hora do "Bom dia!", no formato 24 horas (`08:30`). |
| `evening_time` | Hora do "Boa noite!" (`21:30` = 9h30 da noite). |
| `target_chats` | Nomes das conversas/grupos, **exatamente** como aparecem na sua lista do WhatsApp. |
| `check_interval_seconds` | De quantos em quantos segundos procurar menções. Deixe `12`. |
| `browser_channel` | `"msedge"` para o Microsoft Edge ou `"chrome"` para o Google Chrome. |

> ⚠️ O nome em `target_chats` precisa ser igual ao do WhatsApp, incluindo
> acentos e maiúsculas. Se o grupo se chama "Família ❤️", escreva
> `"Família ❤️"`. Quando o nome não bate, o programa **não** envia nada e
> registra o aviso — ele nunca manda mensagem para a conversa errada.

**Passo 3 — Ligar.** Clique duas vezes em **`start.bat`**. Na primeira vez,
uma janela do navegador vai abrir com um **QR code**: no celular, abra o
WhatsApp → *Dispositivos conectados* → *Conectar dispositivo* e aponte para a
tela. Pronto — isso é feito uma única vez.

Das próximas vezes o programa abre sozinho, sem janela nenhuma aparecendo.

---

## Deixar ligado sempre (opcional)

Para o assistente iniciar junto com o Windows:

1. Aperte `Windows + R`, digite `shell:startup` e dê *Enter*. Uma pasta abre.
2. Clique com o botão direito em **`start.bat`** → *Copiar*.
3. Dentro da pasta que abriu, clique com o botão direito → *Colar atalho*.

Para desfazer, apague o atalho dessa pasta.

## Como desligar o programa

Ele roda invisível, então:

1. Aperte `Ctrl + Shift + Esc` (Gerenciador de Tarefas).
2. Procure **`pythonw.exe`** na lista.
3. Clique nele e depois em *Finalizar tarefa*.

---

## Quando algo não funciona

Tudo que o programa faz fica anotado em um arquivo de texto. Para abrir:
aperte `Windows + R`, cole o caminho abaixo e dê *Enter*.

```
%LOCALAPPDATA%\WhatsAppBotData\assistente.log
```

| Sintoma | O que fazer |
| --- | --- |
| Não recebi o aviso de menção | Confira se `user_name` é o nome que aparece no `@`. Teste as notificações: veja "Comandos extras" abaixo. |
| A mensagem não foi enviada | O nome da conversa provavelmente está diferente do WhatsApp. O log diz qual conversa não foi encontrada. |
| Pediu o QR code de novo | Normal de vez em quando: o WhatsApp desconecta aparelhos antigos. A janela abre sozinha para você escanear. |
| Nada acontece ao clicar em `start.bat` | Rode `setup.bat` novamente e veja se aparece algum erro em vermelho. |
| Abri duas vezes sem querer | Sem problema: o programa detecta e não abre uma segunda cópia. |

### Comandos extras

Para quem estiver ajudando com o computador — abra o Prompt de Comando na
pasta do projeto:

```bat
start.bat --visible                REM mostra a janela do navegador
.venv\Scripts\python.exe main.py --test-notification
.venv\Scripts\python.exe main.py --send-now morning
.venv\Scripts\python.exe main.py --verbose
```

`--send-now morning` manda o "Bom dia!" na hora, útil para confirmar se os
nomes das conversas estão certos. Use `python.exe` (e não `pythonw.exe`) para
ver as mensagens na tela.

---

## Sobre a segurança da sua conta

Este programa controla o **WhatsApp Web** como se fosse você mexendo no mouse e
no teclado. Ele foi feito para chamar o mínimo de atenção possível:

- usa o Edge/Chrome de verdade que já está instalado, com a sua sessão salva;
- digita **letra por letra**, com pausas diferentes a cada tecla;
- espera alguns segundos antes de clicar e antes de enviar;
- envia as saudações com um **atraso aleatório** (10 a 300 segundos) sobre o
  horário marcado, para nunca cair no mesmo segundo todo dia;
- espaça as conversas entre 8 e 25 segundos;
- só olha a lista de conversas a cada 12 segundos, sem recarregar a página.

Ainda assim, vale saber: automatizar o WhatsApp contra os Termos de Uso da Meta
— eles não oferecem uma forma oficial de fazer isso em contas pessoais. O risco
de bloqueio é baixo com o comportamento acima, mas **não é zero**. Mantenha
poucas conversas na lista, horários normais, e não aumente a frequência.

---

## Parte técnica

### Estrutura

```
/
├── config.json          Configuração do usuário
├── requirements.txt     Dependências
├── setup.bat            Cria .venv, instala tudo (espaço de usuário)
├── start.bat            Roda com pythonw.exe (sem console)
├── main.py              Loop principal, logging, trava de instância única
└── src/
    ├── config.py            Leitura + validação do config.json
    ├── whatsapp_driver.py   Playwright: sessão, envio, varredura de menções
    ├── scheduler.py         Horários + jitter, estado em disco
    └── notifier.py          Toasts nativos do Windows
```

### Decisões que valem explicação

**Um único thread.** A API síncrona do Playwright não pode ser chamada de outra
thread. Por isso o agendamento usa `schedule` (que executa os jobs no thread de
quem chama `run_pending()`) e não `APScheduler` com `BackgroundScheduler`, cujos
executores rodam em threads separadas e quebrariam o driver.

**Jitter sem bloquear.** No horário configurado o job é apenas *armado* com um
alvo aleatório; o `tick()` seguinte que passar desse alvo é o que envia. Assim o
atraso de até 5 minutos não paralisa a detecção de menções.

**Nunca headless.** O WhatsApp Web funciona mal sem interface e o fingerprint
headless é trivial de detectar. Para "rodar em segundo plano", a janela é
posicionada fora da área visível (`--window-position=-2400,-2400`) — navegador
real, janela invisível.

**User-Agent não é sobrescrito** quando usamos Edge/Chrome de verdade: um UA
inventado que não combina com o navegador é *mais* detectável que o verdadeiro.
O UA fixo só entra no fallback para o Chromium do Playwright.

**Confirmação antes de digitar.** `send_message()` compara o título no cabeçalho
da conversa aberta com o nome pedido e aborta se forem diferentes. Depois do
envio, confere a última bolha de mensagem enviada; se não confirmar, registra o
aviso e **não** reenvia (duplicar é pior que um log).

**Estado em disco.** `state.json` guarda a data do último envio de cada saudação,
e a marcação acontece *antes* do envio. Reiniciar o programa no meio do dia nunca
duplica um "Bom dia!".

**Janela de atraso.** Se o computador estava suspenso e o horário passou há mais
de 90 minutos (`max_catch_up_minutes`), a saudação é descartada — ninguém manda
"Bom dia!" às três da tarde.

**Trava de instância única.** Dois processos no mesmo perfil do Chrome corrompem
a sessão. `assistente.lock` guarda o PID e é limpo automaticamente se o processo
anterior morreu.

### Seletores do WhatsApp Web

O WhatsApp muda o HTML sem aviso. Todos os seletores estão agrupados no topo de
`src/whatsapp_driver.py`, cada alvo com uma lista de candidatos (do mais estável
para o mais específico); o código usa o primeiro que estiver visível. Se um dia o
envio parar de funcionar, é o único lugar a ajustar.

### Opções não obrigatórias do `config.json`

| Campo | Padrão | Efeito |
| --- | --- | --- |
| `morning_message` | `"Bom dia!"` | Texto da saudação da manhã. |
| `evening_message` | `"Boa noite!"` | Texto da saudação da noite. |
| `mention_names` | `[]` | Apelidos extras que contam como menção. |
| `jitter_min_seconds` / `jitter_max_seconds` | `10` / `300` | Faixa do atraso aleatório. |
| `max_catch_up_minutes` | `90` | Atraso máximo tolerado para ainda enviar. |
| `hide_window` | `true` | `false` mantém a janela do navegador visível. |
| `locale` | `"pt-BR"` | Idioma do navegador. |

### Dados em disco

Tudo em `%LOCALAPPDATA%\WhatsAppBotData\` (nenhuma escrita fora do espaço do
usuário):

```
browser_profile\    sessão do WhatsApp (cookies, localStorage)
assistente.log      log rotativo, 4 arquivos de 1 MB
state.json          data do último envio de cada saudação
assistente.lock     trava de instância única
```

`browser_profile` contém a sessão autenticada da sua conta — trate como senha e
não copie para outro computador.
