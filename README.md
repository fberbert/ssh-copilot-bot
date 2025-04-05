# Telegram Infra Bot

Este projeto é um bot de suporte à infraestrutura do sistema Aivis, que lê e responde mensagens do grupo do Telegram. Ele integra com a API da OpenAI (Threads Beta) para interações avançadas e executa comandos no servidor Ubuntu de forma segura, retornando a saída dos comandos formatada para profissionais de TI.

O projeto utiliza um ambiente virtual Python, possui um arquivo `requirements.txt` com as dependências e inclui um template para o arquivo de serviço systemd (`telegram-infra-bot.service`) para facilitar a execução como um serviço no Ubuntu.

---

## Sumário

- [Recursos](#recursos)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Execução do Bot](#execução-do-bot)
- [Instalação como Serviço](#instalação-como-serviço)
- [Personalização](#personalização)
- [Licença](#licença)

---

## Recursos

- Integração com a API de Threads da OpenAI para manter diálogos contextuais.
- Execução de comandos do servidor de forma segura (sem usar `sudo`).
- Formatação da saída dos comandos via prompt enviado ao assistente, com foco em profissionais de TI.
- Persistência de estado (threads e modo de conversa) em arquivo JSON.
- Modo de conversa ativado automaticamente ao receber menções ou padrões específicos, e desativado automaticamente quando o assistente retorna `#fimdepapo`.

---

## Requisitos

- **Sistema Operacional:** Ubuntu Server 22.04.5 LTS
- **Ambiente Python:** Python 3.10+
- **Dependências:** Listadas no arquivo [`requirements.txt`](requirements.txt)
- **API Key da OpenAI:** Necessária para autenticação junto à API da OpenAI
- **Conta no Telegram:** Para criação e configuração do bot

---

## Instalação

1. **Clone o repositório:**

   ```bash
   git clone <URL_DO_REPOSITORIO>
   cd telegram-infra-bot
   ```

2. **Crie o ambiente virtual:**

   ```bash
   python3 -m venv venv
   ```

3. **Ative o ambiente virtual:**

   ```bash
   source venv/bin/activate
   ```

4. **Instale as dependências:**

   ```bash
   pip install -r requirements.txt
   ```

5. **Configuração das variáveis de ambiente:**

   Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

   ```env
   AIVIS_BOT_TOKEN=seu_token_do_bot
   AIVIS_CHAT_ID=-1001234567890
   OPENAI_API_KEY=sua_api_key_da_openai
   ASSISTANT_ID=seu_assistant_id
   ```

---

## Configuração

- **Estado e Persistência:**  
  O bot salva informações de estado (threads e modo de conversa) em um arquivo chamado `bot_state.json` no diretório do projeto. Certifique-se de que o usuário que executa o bot possui permissão de leitura e escrita neste arquivo.

- **Template do Serviço:**  
  Um template para o arquivo de serviço systemd está disponível no repositório (ex.: `telegram-infra-bot.service`). Este template pode ser ajustado conforme necessário.

---

## Execução do Bot

Para executar o bot manualmente, com o ambiente virtual ativado:

```bash
venv/bin/python infra-bot.py
```

O bot se conectará ao Telegram e ficará aguardando mensagens conforme a lógica implementada.

---

## Instalação como Serviço

Para executar o bot como um serviço no Ubuntu:

1. **Copie o template do arquivo de serviço:**

   Se o template estiver no repositório, copie-o para o diretório de serviços do systemd:

   ```bash
   sudo cp telegram-infra-bot.service /etc/systemd/system/telegram-infra-bot.service
   ```

2. **Edite o arquivo de serviço** (se necessário), garantindo que o `WorkingDirectory` e o caminho do interpretador Python apontem para o diretório do projeto e para o ambiente virtual, respectivamente. Exemplo:

   ```ini
   [Unit]
   Description=Bot de Infraestrutura do Telegram Aivis
   After=network.target

   [Service]
   User=fabio
   Group=fabio
   WorkingDirectory=/home/fabio/projetos/telegram-infra-bot
   ExecStart=/home/fabio/projetos/telegram-infra-bot/venv/bin/python /home/fabio/projetos/telegram-infra-bot/infra-bot.py
   Restart=on-failure
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

3. **Recarregue os arquivos de serviço e inicie o bot:**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-infra-bot.service
   sudo systemctl start telegram-infra-bot.service
   ```

4. **Verifique o status do serviço:**

   ```bash
   sudo systemctl status telegram-infra-bot.service
   ```

---

## Personalização

- **Mensagens do Bot:**  
  O comportamento do bot é definido pelo prompt do assistente e pelas funções de interação. Você pode alterar o prompt, os comandos executados ou a formatação da resposta editando o código no arquivo `infra-bot.py`.

- **Comandos do Servidor:**  
  Os comandos executados pelo bot são seguros e não utilizam `sudo`. Se precisar adicionar novos comandos, edite as funções correspondentes.

- **Formato das Respostas:**  
  As respostas do assistente podem ser enviadas com formatação Markdown. Se desejar ajustar, modifique os parâmetros de envio das mensagens no código.

---

Siga estas instruções para instalar, configurar e executar o bot. Caso encontre problemas, verifique os logs do serviço (ex.: usando `journalctl -u telegram-infra-bot.service`) para mais detalhes.

Boa sorte e aproveite!
