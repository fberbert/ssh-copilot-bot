#!/usr/bin/env markdown
# SSH Copilot Bot

This project is an infrastructure support bot called **ssh-copilot-bot**, used for managing Linux servers via SSH directly from Telegram. It leverages the OpenAI Threads API to maintain contextual conversations and enables secure execution of remote commands, returning formatted outputs tailored for IT professionals.

The project is written in Python, uses a virtual environment, depends on the libraries listed in `requirements.txt`, and includes a service template for running under systemd on Ubuntu.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Available Commands](#available-commands)
- [Running the Bot](#running-the-bot)
- [Installing as a Service](#installing-as-a-service)
- [Customization](#customization)
- [License](#license)

## Features

- Integration with the OpenAI Threads API to maintain contextual dialogues.
- Secure execution of remote SSH commands on servers (without `sudo`).
- Command output formatting with HTML for Telegram.
- State persistence (threads and conversation mode) in JSON files.
- Configuration and authorization commands for granular access control.
- Support for multiple servers per chat, with active selection.

## Requirements

- **Operating System:** Ubuntu Server 22.04.5 LTS (or similar)
- **Python:** 3.10+
- **Dependencies:** Listed in `requirements.txt`
- **OpenAI API Key:** Required to authenticate with the OpenAI API
- **Telegram Bot Token:** A Telegram bot token from BotFather

## Installation

1. **Clone the repository:**
   ```bash
   git clone <REPOSITORY_URL>
   cd ssh-copilot-bot
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv venv
   ```

3. **Activate the virtual environment:**
   ```bash
   source venv/bin/activate
   ```

4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure environment variables:**
   Create a `.env` file based on `env.sample`:
   ```env
   ADMIN_USER=@your_admin_username
   BOT_TOKEN=your_bot_token_here
   REPORT_CHAT_ID=your_report_chat_id
   OPENAI_API_KEY=your_openai_api_key_here
   ASSISTANT_ID=your_assistant_id_here
   ```

6. **Prepare the SSH public key file:**
   Generate a public key if you don't have one:
   ```bash
   ssh-keygen -t rsa -b 4096 -C "your_email@domain.com"
   cat ~/.ssh/id_rsa.pub > bot_key.pub
   ```
   Place `bot_key.pub` in the project root so the bot can display it for adding to remote servers.

## Configuration

- **Persistence:**
  - `bot_state.json`: stores thread and conversation state.
  - `bot_config.json`: stores users, groups, and servers per chat.

- **Server Setup:**
  Configure a server in the chat:
  ```
  /set_server ip=1.2.3.4 port=22 user=ubuntu name=ServerName
  ```
  Then add the contents of `bot_key.pub` to `~/.ssh/authorized_keys` on the remote server.

## Available Commands

- `/help` — Displays general help.
- `/start` — Alias for `/help`.
- `/report` — Generates a full report for the selected server.
- `/set_server` — Adds a new server to the chat (name, IP, port, user).
- `/list_servers` — Lists all configured servers and marks the selected one.
- `/select_server <ServerName>` — Selects which server to use.
- `/server_info [ServerName]` — Shows details for the specified server or all if none is specified.
- `/edit_server <ServerName> ip=... port=... user=...` — Edits a server's configuration.
- `/delete_server <ServerName>` — Removes a server and updates the selection if needed.
- `/grant <id>` — (ADMIN) Grants access. Positive for user, negative for group.
- `/revoke <id>` — (ADMIN) Revokes access. Positive for user, negative for group.

## Running the Bot

To run the bot manually:
```bash
venv/bin/python infra-bot.py
```

## Installing as a Service

1. Copy the service template:
   ```bash
   sudo cp ssh-copilot-bot.service /etc/systemd/system/ssh-copilot-bot.service
   ```

2. Edit the service file `/etc/systemd/system/ssh-copilot-bot.service`, adjusting `User`, `Group`, `WorkingDirectory`, and `ExecStart` to your environment.

3. Reload and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable ssh-copilot-bot.service
   sudo systemctl start ssh-copilot-bot.service
   ```

4. Check logs:
   ```bash
   sudo journalctl -u ssh-copilot-bot.service -f
   ```

## Customization

- **Messages and Commands:** Modify `infra-bot.py` as needed.
- **HTML Formatting:** The bot uses basic HTML tags (`<b>`, `<i>`, `<code>`, etc.) for Telegram messages. Adjust as desired in the source code.

## License

Distributed under the [MIT License](LICENSE).

---

Follow these instructions to install, configure, and operate the SSH Copilot Bot. For questions or issues, refer to logs or contact the bot administrator.

Thank you.
