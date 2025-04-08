# SSH Copilot Bot

Este projeto é um bot de suporte à infraestrutura chamado **ssh-copilot-bot**, utilizado para gerenciamento de servidores Linux via SSH diretamente pelo Telegram. Ele utiliza a API de Threads da OpenAI para manter interações contextuais e permite a execução segura de comandos remotos, retornando saídas formatadas com foco em profissionais de TI.

O projeto é baseado em Python, usa ambiente virtual, depende das bibliotecas listadas em `requirements.txt` e possui um template para ser executado como serviço no Ubuntu via systemd.

## Sumário

- [Recursos](#recursos)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Comandos Disponíveis](#comandos-disponíveis)
- [Execução do Bot](#execução-do-bot)
- [Instalação como Serviço](#instalação-como-serviço)
- [Personalização](#personalização)
- [Licença](#licença)

## Recursos

- Integração com a API de Threads da OpenAI para manter diálogos contextuais.
- Execução segura de comandos em servidores remotos via SSH (sem uso de `sudo`).
- Formatação da saída dos comandos com foco técnico (HTML para Telegram).
- Persistência de estado (threads e modo de conversa) em arquivo JSON.
- Comandos de configuração e autorização para controle granular de acesso.
- Suporte a múltiplos servidores por chat, com seleção ativa.

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
   cd ssh-copilot-bot
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

5. **Configure as variáveis de ambiente:**

   Crie um arquivo `.env` com base no modelo `env.sample`:

   ```env
   ADMIN_USER=@vivaolinux
   BOT_TOKEN=seu_token_do_bot
   REPORT_CHAT_ID=id_do_chat_de_reporte
   OPENAI_API_KEY=sua_api_key_da_openai
   ASSISTANT_ID=seu_assistant_id
   ```

6. **Crie o arquivo com a chave pública:**

   Gere uma chave pública (caso ainda não tenha):

   ```bash
   ssh-keygen -t rsa -b 4096 -C "seu_email@dominio.com"
   cat ~/.ssh/id_rsa.pub > bot_key.pub
   ```

   O arquivo `bot_key.pub` deve estar na raiz do projeto. Ele será mostrado aos usuários para ser adicionado ao servidor remoto.

## Configuração

- **Persistência:**
  - `bot_state.json`: mantém o estado das conversas e threads.
  - `bot_config.json`: armazena usuários, grupos e servidores configurados para cada chat.

- **Servidor:**
  Para configurar um servidor no chat:

  ```
  /set_server ip=1.2.3.4 port=22 user=ubuntu name=MeuServidor
  ```

  Após configurar, adicione a chave pública (`bot_key.pub`) ao arquivo `~/.ssh/authorized_keys` do servidor remoto.

## Comandos Disponíveis

- `/help` — Mostra a ajuda geral com explicações.
- `/start` — Alias para `/help`.
- `/relatorio` — Gera um relatório completo do servidor selecionado.
- `/set_server` — Adiciona um novo servidor ao chat com nome, IP, porta e usuário.
- `/list_servers` — Lista todos os servidores configurados e indica o servidor selecionado.
- `/select_server NomeServidor` — Define qual servidor será usado nas execuções.
- `/server_info` — Mostra todos os servidores ou, se for passado um nome, os detalhes daquele servidor.
- `/edit_server NomeServidor ip=... port=... user=...` — Edita a configuração de um servidor.
- `/delete_server NomeServidor` — Remove um servidor e atualiza o selecionado, se necessário.
- `/grant_user user_id` — (ADMIN) Autoriza um usuário individual.
- `/revoke_user user_id` — (ADMIN) Remove autorização de um usuário.
- `/grant_group group_id` — (ADMIN) Autoriza um grupo.
- `/revoke_group group_id` — (ADMIN) Remove autorização de um grupo.

## Execução do Bot

Para executar o bot manualmente:

```bash
venv/bin/python infra-bot.py
```

## Instalação como Serviço

1. **Copie o template do serviço:**

   ```bash
   sudo cp ssh-copilot-bot.service /etc/systemd/system/ssh-copilot-bot.service
   ```

2. **Edite o serviço:**

   ```ini
   [Unit]
   Description=Bot SSH Copilot
   After=network.target

   [Service]
   User=fabio
   Group=fabio
   WorkingDirectory=/home/fabio/projetos/ssh-copilot-bot
   ExecStart=/home/fabio/projetos/ssh-copilot-bot/venv/bin/python /home/fabio/projetos/ssh-copilot-bot/infra-bot.py
   Restart=on-failure
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

3. **Ative o serviço:**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable ssh-copilot-bot.service
   sudo systemctl start ssh-copilot-bot.service
   ```

4. **Verifique os logs:**

   ```bash
   sudo journalctl -u ssh-copilot-bot.service -f
   ```

## Personalização

- **Mensagens e Comandos:**
  Modifique o arquivo `infra-bot.py` conforme sua necessidade.

- **Formatação HTML:**
  O bot envia mensagens formatadas para o Telegram via HTML simples (`<b>`, `<i>`, `<code>`, etc). Você pode ajustar os blocos de resposta no código-fonte.

## Licença

Distribuído sob a [Licença MIT](LICENSE).

---

Siga estas instruções para instalar, configurar e operar o ssh-copilot-bot. Em caso de dúvidas, envie logs ou mensagens para o administrador do bot. Bom uso!

