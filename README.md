# Telegram Infra Bot

Este projeto é um bot de suporte à infraestrutura que lê e responde mensagens do Telegram. O bot utiliza a API de Threads da OpenAI para interações avançadas e permite a execução segura de comandos em servidores remotos via SSH, retornando a saída dos comandos formatada para profissionais de TI.

O projeto utiliza um ambiente virtual Python, possui um arquivo `requirements.txt` com as dependências e inclui um template para o arquivo de serviço systemd para facilitar a execução como um serviço no Ubuntu.

## Sumário

- [Recursos](#recursos)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Execução do Bot](#execução-do-bot)
- [Instalação como Serviço](#instalação-como-serviço)
- [Personalização](#personalização)
- [Licença](#licença)

## Recursos

- Integração com a API de Threads da OpenAI para manter diálogos contextuais.
- Execução segura de comandos em servidores remotos via SSH (sem uso de `sudo`).
- Formatação da saída dos comandos através de prompts enviados para o assistente, com foco em profissionais de TI.
- Persistência de estado (threads e modo de conversa) em arquivo JSON.
- Modo de conversa que pode ser ativado automaticamente em grupos (por meio de menção ou padrão) ou em chats privados.
- Comandos de autorização e configuração para gerenciar usuários, grupos e dados de acesso ao servidor.

## Requisitos

- **Sistema Operacional:** Ubuntu Server 22.04.5 LTS (ou similar)
- **Python:** 3.10+
- **Dependências:** Listadas no arquivo [`requirements.txt`](requirements.txt)
- **API Key da OpenAI:** Necessária para autenticação junto à API da OpenAI
- **Conta no Telegram:** Para criação e configuração do bot

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

   Crie um arquivo chamado `.env` na raiz do projeto (você pode usar o arquivo `env.sample` como exemplo) e preencha as variáveis conforme necessário:

   ```env
   # Exemplo de .env
   ADMIN_USER=@vivaolinux
   BOT_TOKEN=seu_token_do_bot
   REPORT_CHAT_ID=id_do_chat_de_reporte
   OPENAI_API_KEY=sua_api_key_da_openai
   ASSISTANT_ID=seu_assistant_id
   ```

## Configuração

- **Estado e Persistência:**  
  O bot salva informações de estado (threads e modo de conversa) no arquivo `bot_state.json` e a configuração do bot (usuários, grupos autorizados e dados de acesso ao servidor) no arquivo `bot_config.json`, ambos no diretório do projeto. Verifique se o usuário que executa o bot possui permissões de leitura e escrita nesses arquivos.

- **Configuração do Servidor:**  
  Antes de usar o bot para executar comandos, configure os dados de acesso ao servidor para o chat ou usuário com os comandos:
  
  - `/set_server ip=1.2.3.4 port=22 user=ubuntu`
  - `/set_pubkey <conteúdo_da_chave_pública>`

  **Dica:**  
  Para gerar uma chave pública em um servidor Linux, execute:
  ```bash
  ssh-keygen -t rsa -b 4096 -C "seu_email@dominio.com"
  ```
  Aceite os valores padrão e depois use:
  ```bash
  cat ~/.ssh/id_rsa.pub
  ```
  Copie o conteúdo exibido e use no comando `/set_pubkey`.

- **Autorização:**  
  Se o usuário ou grupo não estiver autorizado (configurado em `bot_config.json`), o bot responderá solicitando que o acesso seja concedido entrando em contato com o administrador (ADMIN_USER) e informando o ID do chat/usuário.

## Execução do Bot

Para executar o bot manualmente com o ambiente virtual ativado:

```bash
venv/bin/python infra-bot.py
```

O bot se conectará ao Telegram e ficará aguardando mensagens conforme a lógica implementada.

## Instalação como Serviço

Para executar o bot como um serviço no Ubuntu:

1. **Copie o template do arquivo de serviço:**

   ```bash
   sudo cp telegram-infra-bot.service /etc/systemd/system/telegram-infra-bot.service
   ```

2. **Edite o arquivo de serviço**, se necessário, garantindo que o `WorkingDirectory` e o caminho do interpretador Python apontem para o diretório do projeto e para o ambiente virtual, respectivamente. Exemplo:

   ```ini
   [Unit]
   Description=Bot de Infraestrutura do Telegram
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

3. **Recarregue e inicie o serviço:**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-infra-bot.service
   sudo systemctl start telegram-infra-bot.service
   ```

4. **Verifique o status:**

   ```bash
   sudo systemctl status telegram-infra-bot.service
   ```

## Personalização

- **Mensagens e Comandos:**  
  Você pode alterar os prompts, comandos e lógica de autorização editando o arquivo `infra-bot.py`.

- **Formato das Respostas:**  
  As respostas são enviadas com formatação Markdown. Caso queira ajustar, modifique os parâmetros de envio das mensagens no código.

## Licença

Este projeto é distribuído sob a [Licença MIT](LICENSE).

---

Siga estas instruções para instalar, configurar e executar o bot. Em caso de dúvidas ou problemas, verifique os logs do serviço (por exemplo, usando `journalctl -u telegram-infra-bot.service`).

Boa sorte e aproveite!
