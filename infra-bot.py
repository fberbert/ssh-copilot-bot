#!/usr/bin/env python3

import logging
import subprocess
import os
import re
import asyncio
import json
import bleach
import asyncssh
import asyncio, concurrent.futures
from dotenv import load_dotenv

load_dotenv()

import openai
from telegram import ForceReply, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Load environment variables
openai.api_key = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")  # e.g. "-1001234567890"
ADMIN_USER = os.getenv("ADMIN_USER")  # admin username

# Project directory and configuration files
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(PROJECT_DIR, "bot_key.pub")   # Public key file for the bot to use in SSH
STATE_FILE = os.path.join(PROJECT_DIR, "bot_state.json")  # State of threads and conversation mode
BOT_CONFIG_FILE = os.path.join(PROJECT_DIR, "bot_config.json")  # Bot configuration

# State and configuration structures
DATA = {
    "threads": {},  # chat_id -> thread_id
    "talking": {}   # chat_id -> bool (conversation mode)
}
CONFIG = {
    "authorized_users": [],
    "authorized_groups": [],
    "servers": {}  # str(chat_id) -> { "selected_server": str, "servers": { serverName -> { ip, port, user } } }
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ================
# Load / Save state (bot_state.json)
# ================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                DATA["threads"] = saved.get("threads", {})
                DATA["talking"] = saved.get("talking", {})
                logger.info("State loaded from %s", STATE_FILE)
        except Exception as e:
            logger.warning("Could not load state from %s: %s", STATE_FILE, e)
    else:
        logger.info("State file %s does not exist; starting empty.", STATE_FILE)

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, indent=2)
        logger.info("State saved to %s", STATE_FILE)
    except Exception as e:
        logger.error("Error saving state to %s: %s", STATE_FILE, e)

# ================
# Load / Save config (bot_config.json)
# ================
def load_config():
    global CONFIG
    if os.path.exists(BOT_CONFIG_FILE):
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                CONFIG = json.load(f)
            logger.info("Config loaded from %s", BOT_CONFIG_FILE)
        except Exception as e:
            logger.warning("Could not load config from %s: %s", BOT_CONFIG_FILE, e)
    else:
        logger.info("Config file %s does not exist; starting empty.", BOT_CONFIG_FILE)

def save_config():
    try:
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
        logger.info("Config saved to %s", BOT_CONFIG_FILE)
    except Exception as e:
        logger.error("Error saving config to %s: %s", BOT_CONFIG_FILE, e)

# ================
# Sanitization and text splitting functions
# ================
def sanitize_html(text: str) -> str:
    # Convert <br> to newline
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Convert <p> and </p> to newline
    text = re.sub(r'\s*<\s*/?\s*p\s*>\s*', '\n', text, flags=re.IGNORECASE)
    # Remove <span> tags
    text = re.sub(r'<span[^>]*?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</span>', '', text, flags=re.IGNORECASE)
    # Remove <pre><code ...> and </code></pre> if both exist
    if re.search(r'<pre><code[^>]*?>', text, flags=re.IGNORECASE) and re.search(r'</code></pre>', text, flags=re.IGNORECASE):
        text = re.sub(r'<pre><code[^>]*?>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</code></pre>', '', text, flags=re.IGNORECASE)
    
    # Allow only certain tags
    allowed_tags = ['b', 'i', 'code', 'pre', 'a']
    text = bleach.clean(text, tags=allowed_tags, strip=True)

    return text

def split_into_chunks(text: str, chunk_size: int = 4096) -> list[str]:
    return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)]

# ================
# OpenAI: thread and message creation and handling
# ================
def find_or_create_thread(chat_id: int) -> str:
    logger.info("Searching for thread for chat_id %s", chat_id)
    cid_str = str(chat_id)
    if cid_str in DATA["threads"]:
        return DATA["threads"][cid_str]
    resp = openai.beta.threads.create()
    thread_id = resp.id
    DATA["threads"][cid_str] = thread_id
    save_state()
    return thread_id


def wait_for_run_to_finish(thread_id: str, timeout: int = 60):
    """Block until any run is no longer in 'queued' or 'in_progress' status."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        runs = openai.beta.threads.runs.list(thread_id=thread_id).data

        # ➊ No runs yet: we can send the message without waiting.
        if not runs:
            return

        latest = runs[0]
        if latest.status not in ("queued", "in_progress"):
            return                          # ➋ The last run has already completed.

        time.sleep(1)

    raise TimeoutError("Timeout waiting for the active run to finish.")


def send_message_to_thread(thread_id, role, content):
    """
    Send a message to the thread. If content exceeds 256000 characters,
    split into parts and send sequentially. Waits for active runs to finish.
    """
    wait_for_run_to_finish(thread_id)

    max_length = 256000
    responses = []
    if len(content) > max_length:
        parts = [content[i: i + max_length] for i in range(0, len(content), max_length)]
        for part in parts:
            resp = openai.beta.threads.messages.create(
                thread_id=thread_id,
                role=role,
                content=part
            )
            responses.append(resp)
        return responses[-1]
    else:
        resp = openai.beta.threads.messages.create(
            thread_id=thread_id,
            role=role,
            content=content
        )
        return resp


def run_assistant(thread_id):
    """
    Create and start a new assistant run in the given thread. Returns the run ID.
    """
    resp = openai.beta.threads.runs.create(thread_id, assistant_id=ASSISTANT_ID)
    return resp.id

def poll_for_response(thread_id, run_id, timeout=60):
    import time
    start = time.time()
    while time.time() - start < timeout:
        run_status = openai.beta.threads.runs.retrieve(run_id=run_id, thread_id=thread_id)
        if run_status.status == "completed":
            msgs = openai.beta.threads.messages.list(thread_id=thread_id)
            sorted_msgs = sorted(msgs.data, key=lambda m: m.created_at, reverse=True)
            for msg in sorted_msgs:
                if msg.role == "assistant":
                    blocks = []
                    for b in msg.content:
                        if b.type == "text":
                            blocks.append(b.text.value)
                    return "\n".join(blocks)
        time.sleep(2)
    return "Timeout: unable to retrieve response"

# ================
# SSH and command execution
# ================
async def async_run_command(chat_id: int, command: str) -> str:
    cid_str = str(chat_id)

    # Check if any server is configured for this chat
    if cid_str not in CONFIG["servers"]:
        return (
            "No server configured for this chat.\n"
            "Please configure a server using:\n"
            "/set_server ip=1.2.3.4 port=22 user=ubuntu name=ServerName\n"
            "Then, add the contents of bot_key.pub to ~/.ssh/authorized_keys on the server."
        )
    # Check if a server is selected
    selected_server = CONFIG["servers"][cid_str].get("selected_server")
    if not selected_server:
        return (
            "No server is selected for this chat.\n"
            "Use /select_server <ServerName> or configure a new server with /set_server."
        )

    # Fetch selected server information
    servers_dict = CONFIG["servers"][cid_str].get("servers", {})
    if selected_server not in servers_dict:
        return (
            f"The server '{selected_server}' no longer exists or was not configured correctly.\n"
            "Use /list_servers to check."
        )

    server_info = servers_dict[selected_server]
    ip = server_info["ip"]
    port = int(server_info["port"])
    user = server_info["user"]

    # Prepare the command
    safe_cmd = command
    logger.info(f"Executing command '{safe_cmd}' on server {user}@{ip}:{port} via AsyncSSH")
    logger.info(f"Original command: {command}")
    try:
        async with asyncssh.connect(ip, port=port, username=user, known_hosts=None) as conn:
            result = await conn.run(safe_cmd, check=True)
            return result.stdout.strip()
    except Exception as e:
        return f"Error executing command via AsyncSSH: {e}"



# ================
# Authorization checking
# ================
def is_authorized(update: Update) -> bool:
    chat = update.effective_chat
    chat_id = chat.id
    username = f"@{update.effective_user.username}"
    if chat.type in ["group", "supergroup"]:
        return (chat_id in CONFIG["authorized_groups"])
    else:
        user_id = update.effective_user.id
        return (user_id in CONFIG["authorized_users"] or username == ADMIN_USER)

def request_authorization_message(update: Update) -> str:
    chat_id = update.effective_chat.id
    return (
        f"You are not authorized to use this bot.\n"
        f"Please request access from {ADMIN_USER}, providing the Chat ID: {chat_id}."
    )

# ================
# Server management commands
# ================
async def set_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Configure a new server for this chat.
    Example: /set_server ip=1.2.3.4 port=22 user=ubuntu name=ServerName
    After configuration, the bot will display its public key to add to ~/.ssh/authorized_keys on the server.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    text = update.message.text.replace("/set_server", "").strip()
    parts = text.split()
    server_data = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            server_data[k.strip().lower()] = v.strip()

    # Check required parameters
    if not all(k in server_data for k in ["ip", "port", "user", "name"]):
        await update.message.reply_text(
            "All parameters are required:\n"
            "Usage: /set_server ip=1.2.3.4 port=22 user=ubuntu name=ServerName"
        )
        return

    cid_str = str(chat_id)
    if cid_str not in CONFIG["servers"]:
        CONFIG["servers"][cid_str] = {
            "selected_server": None,
            "servers": {}
        }

    server_name = server_data["name"]
    CONFIG["servers"][cid_str]["servers"][server_name] = {
        "ip": server_data["ip"],
        "port": server_data["port"],
        "user": server_data["user"]
    }

    # Whenever a new server is added, it becomes the selected server
    CONFIG["servers"][cid_str]["selected_server"] = server_name

    save_config()
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception:
        key_content = "Could not read the bot_key.pub file."

    reply = (
        f"Server <b>{server_name}</b> has been successfully configured and is now selected.\n\n"
        "To enable passwordless SSH, add the key below to <i>~/.ssh/authorized_keys</i> "
        "on the target server:\n\n"
        f"<pre>{key_content}</pre>"
    )
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def list_servers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    List all servers configured for this chat.
    Displays a marker next to the currently selected server.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    if cid_str not in CONFIG["servers"] or not CONFIG["servers"][cid_str].get("servers"):
        await update.message.reply_text("No servers configured for this chat.")
        return

    selected_server = CONFIG["servers"][cid_str].get("selected_server")
    servers = CONFIG["servers"][cid_str]["servers"]
    reply_lines = ["Configured servers for this chat:"]
    for name, info in servers.items():
        if name == selected_server:
            reply_lines.append(
                f"- <b>{name}</b> (selected): {info.get('ip')}:{info.get('port')} ({info.get('user')})"
            )
        else:
            reply_lines.append(
                f"- <b>{name}</b>: {info.get('ip')}:{info.get('port')} ({info.get('user')})"
            )

    reply = "\n".join(reply_lines)
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def server_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display detailed information for a specific server if a name is provided.
    If no name is given, list all configured servers.
    Usage: /server_info [ServerName]
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) > 1:
        server_name = args[1]
        if (
            cid_str in CONFIG["servers"]
            and CONFIG["servers"][cid_str].get("servers")
            and server_name in CONFIG["servers"][cid_str]["servers"]
        ):
            info = CONFIG["servers"][cid_str]["servers"][server_name]
            reply = (
                f"Server <b>{server_name}</b> information:\n"
                f"IP: <b>{info.get('ip', 'N/A')}</b>\n"
                f"Port: <b>{info.get('port', 'N/A')}</b>\n"
                f"User: <b>{info.get('user', 'N/A')}</b>"
            )
        else:
            reply = f"No server found with name <b>{server_name}</b>."
    else:
        # If no argument is given, list the servers
        if cid_str in CONFIG["servers"] and CONFIG["servers"][cid_str].get("servers"):
            servers = CONFIG["servers"][cid_str]["servers"]
            selected_server = CONFIG["servers"][cid_str].get("selected_server")
            lines = ["Servers configured in this chat:"]
            for name, info in servers.items():
                if name == selected_server:
                    lines.append(f"- <b>{name}</b> (selected): {info['ip']}:{info['port']} ({info['user']})")
                else:
                    lines.append(f"- <b>{name}</b>: {info['ip']}:{info['port']} ({info['user']})")
            reply = "\n".join(lines)
        else:
            reply = "No servers configured for this chat."
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def edit_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Edit the configuration of an existing server.
    Usage: /edit_server <ServerName> ip=... port=... user=...
    At least one parameter (ip, port, or user) must be provided.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /edit_server <ServerName> ip=... port=... user=..."
        )
        return

    server_name = args[1]
    if (
        cid_str not in CONFIG["servers"]
        or not CONFIG["servers"][cid_str].get("servers")
        or server_name not in CONFIG["servers"][cid_str]["servers"]
    ):
        await update.message.reply_text(f"Server '{server_name}' not found.")
        return

    server_data = {}
    for p in args[2:]:
        if "=" in p:
            k, v = p.split("=", 1)
            server_data[k.strip().lower()] = v.strip()

    if not any(k in server_data for k in ["ip", "port", "user"]):
        await update.message.reply_text(
            "Please provide at least one parameter to update (ip, port, or user)."
        )
        return

    CONFIG["servers"][cid_str]["servers"][server_name].update(server_data)
    save_config()
    await update.message.reply_text(
        f"Server '{server_name}' updated successfully.",
        parse_mode="HTML"
    )


async def delete_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete a server configuration.
    Usage: /delete_server <ServerName>
    If the deleted server was selected, the selection will be cleared or moved to another server if available.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Usage: /delete_server <ServerName>")
        return

    server_name = args[1]
    if (
        cid_str in CONFIG["servers"]
        and CONFIG["servers"][cid_str].get("servers")
        and server_name in CONFIG["servers"][cid_str]["servers"]
    ):
        # If it's the selected server, clear the selection
        if CONFIG["servers"][cid_str].get("selected_server") == server_name:
            CONFIG["servers"][cid_str]["selected_server"] = None

        del CONFIG["servers"][cid_str]["servers"][server_name]
        # If servers remain, select the first one (arbitrary) as default
        if CONFIG["servers"][cid_str]["servers"]:
            some_server = list(CONFIG["servers"][cid_str]["servers"].keys())[0]
            CONFIG["servers"][cid_str]["selected_server"] = some_server

        save_config()
        await update.message.reply_text(
            f"Server '{server_name}' deleted successfully.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(f"Server '{server_name}' not found.")

async def select_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Select which server to use for this chat.
    Usage: /select_server <ServerName>
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Usage: /select_server <ServerName>")
        return

    server_name = args[1]
    if (
        cid_str not in CONFIG["servers"]
        or not CONFIG["servers"][cid_str].get("servers")
        or server_name not in CONFIG["servers"][cid_str]["servers"]
    ):
        await update.message.reply_text(f"Server '{server_name}' not found. Use /list_servers to check available servers.")
        return

    CONFIG["servers"][cid_str]["selected_server"] = server_name
    save_config()
    await update.message.reply_text(f"Server '{server_name}' is now selected.", parse_mode="HTML")


# ================
# Authorization commands (ADMIN)
# ================
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text(
            f"Only the administrator ({ADMIN_USER}) can execute this command."
        )
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Usage: /grant <id>")
        return
    try:
        target_id = int(args[1])
    except:
        await update.message.reply_text(
            "Invalid ID. Example: /grant 12345 or /grant -1001234567890"
        )
        return
    if target_id >= 0:
        if target_id not in CONFIG["authorized_users"]:
            CONFIG["authorized_users"].append(target_id)
            save_config()
            await update.message.reply_text(
                f"User <b>{target_id}</b> added to authorized users.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"User <b>{target_id}</b> was already authorized.",
                parse_mode="HTML"
            )
    else:
        if target_id not in CONFIG["authorized_groups"]:
            CONFIG["authorized_groups"].append(target_id)
            save_config()
            await update.message.reply_text(
                f"Group <b>{target_id}</b> added to authorized groups.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"Group <b>{target_id}</b> was already authorized.",
                parse_mode="HTML"
            )

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revoke access for a user or group.
    Usage: /revoke <id> (positive for user, negative for group)"""
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text(
            f"Only the administrator ({ADMIN_USER}) can execute this command."
        )
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Usage: /revoke <id>")
        return
    try:
        target_id = int(args[1])
    except:
        await update.message.reply_text(
            "Invalid ID. Example: /revoke 12345 or /revoke -1001234567890"
        )
        return
    if target_id >= 0:
        if target_id in CONFIG["authorized_users"]:
            CONFIG["authorized_users"].remove(target_id)
            save_config()
            await update.message.reply_text(
                f"User <b>{target_id}</b> removed from authorized users.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"User <b>{target_id}</b> was not in authorized users.",
                parse_mode="HTML"
            )
    else:
        if target_id in CONFIG["authorized_groups"]:
            CONFIG["authorized_groups"].remove(target_id)
            save_config()
            await update.message.reply_text(
                f"Group <b>{target_id}</b> removed from authorized groups.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"Group <b>{target_id}</b> was not in authorized groups.",
                parse_mode="HTML"
            )

# ================
# General commands
# ================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display the help message.
    """
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception as e:
        key_content = "Could not read the bot_key.pub file."
    
    help_text = (
        "Available commands:<br><br>\n"
        f"<b>/help</b> - Show this help message<br>\n"
        f"<b>/set_server</b> - Configure a new server for this chat. Example:<br>\n"
        f"  <code>/set_server ip=1.2.3.4 port=22 user=ubuntu name=ServerName</code><br>\n"
        f"<b>/list_servers</b> - List all servers configured for this chat.<br>\n"
        f"<b>/select_server &lt;ServerName&gt;</b> - Select which server to use for commands.<br>\n"
        f"<b>/server_info [ServerName]</b> - Show server details or list all if no name is provided.<br>\n"
        f"<b>/edit_server &lt;ServerName&gt; ip=... port=... user=...</b> - Edit a server's configuration.<br>\n"
        f"<b>/delete_server &lt;ServerName&gt;</b> - Delete a configured server.<br>\n"
        f"<b>/grant</b> &lt;id&gt; - (Admin only). Positive for user, negative for group<br>\n"
        f"<b>/revoke</b> &lt;id&gt; - (Admin only). Positive for user, negative for group<br>\n"
        f"<b>/delete_thread</b> - Delete the current conversation thread and start a new fresh one.<br><br>\n"
        f"Notes:<br><br>\n"
        f"- In private chats, all messages are handled by the bot directly.<br>\n"
        f"- In groups, mention the bot (@{BOT_USERNAME}) or include 'ssh-copilot-bot' to initiate a conversation.<br>\n"
        f"- Before using the bot (except /help), configure at least one server with <b>/set_server</b>.<br><br>\n"
        f"This is the bot's public key (<i>bot_key.pub</i>):<br><br>\n"
        f"<pre>{key_content}</pre><br>\n"
        f"Add this key to <i>~/.ssh/authorized_keys</i> on your server.<br><br>\n"
        f"For questions, contact the bot developer on Telegram: @your_admin_username"
    )
    await update.message.reply_text(sanitize_html(help_text), parse_mode="HTML")
    await turn_on_talking(update, context)

# ================
# Thread management commands
# ================
async def delete_thread_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete the current OpenAI thread for this chat and start a new fresh thread.
    """
    chat_id = update.effective_chat.id
    cid_str = str(chat_id)
    if cid_str in DATA["threads"]:
        del DATA["threads"][cid_str]
        save_state()
    new_thread_id = find_or_create_thread(chat_id)
    await update.message.reply_text(
        f"Current conversation thread has been deleted and a new fresh thread started.\n"
        f"Use this command when your thread grows too large and consumes many tokens."
    )


async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the main dialog with the user, including interaction with ChatGPT.
    If the assistant response contains "cmd:", execute the command on the selected server.
    """
    if not is_authorized(update):
        msg = request_authorization_message(update)
        await update.message.reply_text(sanitize_html(msg))
        return

    chat_id = update.effective_chat.id
    original_text = update.message.text
    user = update.effective_user
    uname = user.username
    fullname = user.full_name or user.first_name or "Unknown Name"
    user_msg = f"[{fullname} ({uname})] {original_text}"
    logger.info("Received message in chat %s: %s", chat_id, user_msg)

    thread_id = await asyncio.to_thread(find_or_create_thread, chat_id)
    await asyncio.to_thread(send_message_to_thread, thread_id, "user", user_msg)
    run_id = await asyncio.to_thread(run_assistant, thread_id)
    assistant_reply = await asyncio.to_thread(poll_for_response, thread_id, run_id)
    logger.info("AI response in chat %s: %s", chat_id, assistant_reply)

    # If the response contains "cmd:", it means the bot requested executing a command via SSH
    if "cmd:" in assistant_reply.lower():
        command = assistant_reply.split("cmd:")[1].strip()
        command_output = await async_run_command(chat_id, command)
        prompt = (
            "Here is the command output. Format the response below concisely and technically, "
            "using simple HTML tags (only i, b, code, pre, a) for sending via Telegram, "
            "explaining the result:\n\n"
            "Command output:\n" + command_output
        )
        await asyncio.to_thread(send_message_to_thread, thread_id, "user", prompt)
        new_run_id = await asyncio.to_thread(run_assistant, thread_id)
        formatted_reply = await asyncio.to_thread(poll_for_response, thread_id, new_run_id)
        for chunk in split_into_chunks(formatted_reply, 4096):
            await update.message.reply_text(sanitize_html(chunk), parse_mode="HTML", disable_web_page_preview=True)
        return

    # If the response contains "#endchat", end conversation mode
    if "#endchat" in assistant_reply.lower():
        DATA["talking"][str(chat_id)] = False
        save_state()
        await update.message.reply_text(
            "Ending interactive mode. If you need anything else, mention me or use /help."
        )
        return

    # Otherwise, just display the response
    for chunk in split_into_chunks(assistant_reply, 4096):
        sanitized_chunk = sanitize_html(chunk)
        await update.message.reply_text(sanitized_chunk, parse_mode="HTML", disable_web_page_preview=True)

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for private chat messages. All user messages are forwarded to the talk() function.
    """
    await talk(update, context)

async def mention_or_regex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for group messages that mention the bot or match the pattern 'ssh-copilot-bot'.
    Activates conversation mode and calls the talk() function.
    """
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()
    await talk(update, context)

async def turn_on_talking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Enable conversation mode for the chat.
    """
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Generic handler for group messages. If conversation mode is active, forward to talk().
    """
    chat_id = update.effective_chat.id
    if DATA["talking"].get(str(chat_id), False):
        await talk(update, context)

# ================
# Main function
# ================
def main() -> None:
    load_state()
    load_config()

    # Larger thread pool for OpenAI/IO calls (optional)
    thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_default_executor(thread_pool)

    # Bot creation
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)      # ← allow multiple simultaneous updates
        .build()
    )


    # Admin commands
    application.add_handler(CommandHandler("grant", grant))
    application.add_handler(CommandHandler("revoke", revoke))

    # Server configuration commands
    application.add_handler(CommandHandler("set_server", set_server_command))
    application.add_handler(CommandHandler("list_servers", list_servers_command))
    application.add_handler(CommandHandler("edit_server", edit_server_command))
    application.add_handler(CommandHandler("delete_server", delete_server_command))
    application.add_handler(CommandHandler("select_server", select_server_command))

    # Other commands
    application.add_handler(CommandHandler("server_info", server_info_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("start", help_command))

    # Thread management commands
    application.add_handler(CommandHandler("delete_thread", delete_thread_command))

    # Handlers for messages:
    # 1) Private chats: any message goes to private_message_handler
    private_filter = filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(private_filter, private_message_handler))

    # 2) In groups, to start interaction, mention the bot or include the keyword 'ssh-copilot-bot'
    mention_filter = (
        filters.Mention(BOT_USERNAME)
        | filters.Regex(re.compile(r"ssh[- ]?copilot[- ]?bot", re.IGNORECASE))
    )
    group_filter = filters.ChatType.GROUPS
    application.add_handler(MessageHandler(mention_filter & group_filter, mention_or_regex_handler))

    # 3) Group messages after conversation mode is active:
    application.add_handler(MessageHandler(group_filter, handle_any_message))
    
    application.run_polling()

if __name__ == "__main__":
    main()

