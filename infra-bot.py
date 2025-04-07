#!/usr/bin/env python3

import logging
import subprocess
import os
import re
import asyncio
import json
import bleach
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
from apscheduler.schedulers.background import BackgroundScheduler

# Carrega variáveis de ambiente
openai.api_key = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")  # Ex: "-1001234567890"
ADMIN_USER = os.getenv("ADMIN_USER")  # username do admin

# Diretório do projeto e arquivos de configuração
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(PROJECT_DIR, "bot_key.pub")  # Arquivo da chave privada a ser usado no SSH
STATE_FILE = os.path.join(PROJECT_DIR, "bot_state.json")  # Estado das threads e modo de conversa
BOT_CONFIG_FILE = os.path.join(PROJECT_DIR, "bot_config.json")  # Configuração do bot

# Estruturas de estado e configuração
DATA = {
    "threads": {},  # chat_id -> thread_id
    "talking": {}   # chat_id -> bool (modo conversa)
}
CONFIG = {
    "authorized_users": [],
    "authorized_groups": [],
    "servers": {}   # str(chat_id) -> {ip, port, user}
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ================
# Carregar / Salvar state (bot_state.json)
# ================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                DATA["threads"] = saved.get("threads", {})
                DATA["talking"] = saved.get("talking", {})
                logger.info("Estado carregado de %s", STATE_FILE)
        except Exception as e:
            logger.warning("Não foi possível carregar o estado de %s: %s", STATE_FILE, e)
    else:
        logger.info("Arquivo de estado %s não existe; iniciando vazio.", STATE_FILE)

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, indent=2)
        logger.info("Estado salvo em %s", STATE_FILE)
    except Exception as e:
        logger.error("Erro ao salvar estado em %s: %s", STATE_FILE, e)

# ================
# Carregar / Salvar config (bot_config.json)
# ================
def load_config():
    global CONFIG
    if os.path.exists(BOT_CONFIG_FILE):
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                CONFIG = json.load(f)
            logger.info("Config carregada de %s", BOT_CONFIG_FILE)
        except Exception as e:
            logger.warning("Não foi possível carregar config de %s: %s", BOT_CONFIG_FILE, e)
    else:
        logger.info("Arquivo de config %s não existe; iniciando vazio.", BOT_CONFIG_FILE)

def save_config():
    try:
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
        logger.info("Config salva em %s", BOT_CONFIG_FILE)
    except Exception as e:
        logger.error("Erro ao salvar config em %s: %s", BOT_CONFIG_FILE, e)

# ================
# SSH e execução de comandos
# ================
def rodar_comando(chat_id: int, comando: str):
    """
    Executa o comando via SSH no servidor associado ao chat_id em CONFIG["servers"].
    Se não houver servidor configurado, retorna uma mensagem solicitando configuração.
    O comando SSH utiliza a chave privada contida no arquivo KEY_FILE para autenticação.
    """
    cid_str = str(chat_id)
    if cid_str not in CONFIG["servers"] or not all(k in CONFIG["servers"][cid_str] for k in ["ip", "port", "user"]):
        return (
            "Nenhum servidor configurado para este grupo/usuário.\n"
            "Por favor, configure o servidor usando:\n"
            "/set_server ip=1.2.3.4 port=22 user=ubuntu\n"
            "E, depois, adicione o conteúdo de bot_key.pub no arquivo ~/.ssh/authorized_keys do servidor de destino."
        )
    server_info = CONFIG["servers"][cid_str]
    ip = server_info["ip"]
    port = server_info["port"]
    user = server_info["user"]

    # Escapa aspas simples internas para serem interpretadas corretamente dentro de aspas simples do shell
    safe_cmd = comando.replace("'", "'\"'\"'")
    logger.info(f"Executando comando '{comando}' no servidor {ip} ({user}@{ip}:{port})")
    ssh_cmd = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-i {KEY_FILE} {user}@{ip} -p {port} '{safe_cmd}'"
    )

    try:
        saida = subprocess.check_output(ssh_cmd, shell=True, text=True)
        return saida.strip()
    except subprocess.CalledProcessError as e:
        return f"Erro ao executar '{ssh_cmd}': {e}. Falha na comunicação com o servidor."

def sanitize_html(text: str) -> str:
    # Replace <br> (or variations) with newline characters
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Substitui <p> e </p> por nova linha, mesmo que haja espaços ou indentação
    text = re.sub(r'\s*<\s*/?\s*p\s*>\s*', '\n', text, flags=re.IGNORECASE)

    # Remove <span> e </span> tags
    text = re.sub(r'<span[^>]*?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</span>', '', text, flags=re.IGNORECASE)

    # Remove <pre><code ...> and </code></pre> tags only if both are present
    if re.search(r'<pre><code[^>]*?>', text, flags=re.IGNORECASE) and re.search(r'</code></pre>', text, flags=re.IGNORECASE):
        text = re.sub(r'<pre><code[^>]*?>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</code></pre>', '', text, flags=re.IGNORECASE)
    
    # user o bleach para remover tags HTML indesejadas
    allowed_tags = ['b', 'i', 'code', 'pre']
    text = bleach.clean(text, tags=allowed_tags, strip=True)

    return text

# ================
# Relatórios
# ================
def gerar_relatorio_humanizado():
    cid = int(REPORT_CHAT_ID)
    output_df = rodar_comando(cid, "df -h")
    output_backups = rodar_comando(cid, "/home/fabio/bin/listar-backups")
    output_snaps = rodar_comando(cid, "/home/fabio/bin/listar-snapshots")
    output_apache = rodar_comando(cid, "service apache2 status")
    output_mysql = rodar_comando(cid, "service mysql status")

    prompt = (
        "Abaixo estão as saídas dos comandos referentes a diferentes aspectos do servidor. "
        "Formate essas informações de forma concisa e técnica" 
        f"Espaço em disco (df -h):\n{output_df}\n\n"
        f"Backups no AWS S3:\n{output_backups}\n\n"
        f"Snapshot EC2:\n{output_snaps}\n\n"
        f"Apache2 status:\n{output_apache}\n\n"
        f"MySQL status:\n{output_mysql}"
    )

    thread_id = find_or_create_thread(cid)
    send_message_to_thread(thread_id, "user", prompt)
    run_id = run_assistant(thread_id)
    answer = poll_for_response(thread_id, run_id)
    return answer

def job_enviar_relatorio(app):
    text = gerar_relatorio_humanizado()
    logger.info("Enviando relatório diário para o chat %s", REPORT_CHAT_ID)
    logger.info("Conteúdo do relatório:\n%s", text)
    app.bot.send_message(chat_id=REPORT_CHAT_ID, text=text, parse_mode="HTML")

async def command_enviar_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await asyncio.to_thread(gerar_relatorio_humanizado)
    logger.info("Enviando relatório diário para o chat %s", REPORT_CHAT_ID)
    logger.info("Conteúdo do relatório:\n%s", text)
    # Envia o relatório para o chat de relatório
    await update.message.reply_text(sanitize_html(text), parse_mode="HTML", disable_web_page_preview=True)
    await turn_on_talking(update, context)

# ================
# OpenAI Threads
# ================
def find_or_create_thread(chat_id: int) -> str:
    cid_str = str(chat_id)
    if cid_str in DATA["threads"]:
        return DATA["threads"][cid_str]
    resp = openai.beta.threads.create()
    thread_id = resp.id
    DATA["threads"][cid_str] = thread_id
    save_state()
    return thread_id

def send_message_to_thread(thread_id, role, content):
    """
    Envia uma mensagem à thread. Se o conteúdo exceder 256000 caracteres,
    divide em partes e envia sequencialmente.
    """
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
        return responses[-1]  # Retorna a última resposta para continuidade
    else:
        resp = openai.beta.threads.messages.create(
            thread_id=thread_id,
            role=role,
            content=content
        )
        return resp

def run_assistant(thread_id):
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
    return "Timeout: não foi possível obter resposta"

# ================
# Verificação de autorização
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
        f"Você não está autorizado a usar este bot.\n"
        f"Solicite autorização a {ADMIN_USER}, informando o ID: {chat_id}"
    )

# ================
# Setup de servidor
# ================
async def set_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Configura o acesso ao servidor para este chat/grupo.
    Uso: /set_server ip=1.2.3.4 port=22 user=ubuntu
    Todos os parâmetros são obrigatórios.
    Após configurar, o bot exibirá o conteúdo do arquivo bot_key.pub e instruirá como adicioná-lo ao arquivo ~/.ssh/authorized_keys do servidor de destino.
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
            server_data[k] = v

    if not all(k in server_data for k in ["ip", "port", "user"]):
        await update.message.reply_text(
            "Parâmetros obrigatórios não informados.\nUso correto: /set_server ip=1.2.3.4 port=22 user=ubuntu"
        )
        return

    cid_str = str(update.effective_chat.id)
    if cid_str not in CONFIG["servers"]:
        CONFIG["servers"][cid_str] = {}

    for key in ["ip", "port", "user"]:
        CONFIG["servers"][cid_str][key] = server_data[key]

    save_config()
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception as e:
        key_content = "Não foi possível ler o arquivo bot_key.pub."
    reply = (
        "Configurações do servidor atualizadas com sucesso.\n\n"
        "Para que o bot autentique via SSH sem senha, adicione a chave abaixo ao arquivo ~/.ssh/authorized_keys do servidor de destino:\n\n"
        f"{key_content}"
    )
    await update.message.reply_text(reply)

# ================
# Comando /server_info
# ================
async def server_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Exibe as informações do servidor configurado para este chat, se houver.
    """
    chat_id = update.effective_chat.id
    cid_str = str(chat_id)
    if cid_str in CONFIG["servers"]:
        server_info = CONFIG["servers"][cid_str]
        info_text = (
            "Server configuration for this chat:\n"
            f"IP: <b>{server_info.get('ip', 'N/A')}</b><br>\n"
            f"Port: <b>{server_info.get('port', 'N/A')}</b><br>\n"
            f"User: <b>{server_info.get('user', 'N/A')}</b>\n"
        )
    else:
        info_text = "No server configured for this chat."
    await update.message.reply_text(info_text, parse_mode="HTML")

# ================
# Comandos de Grant / Revoke
# ================
async def grant_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text(f"Apenas o administrador pode executar este comando ({ADMIN_USER}).")
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /grant_user <user_id>")
        return
    try:
        user_id = int(args[1])
    except:
        await update.message.reply_text("ID inválido. Ex.: /grant_user 12345")
        return
    if user_id not in CONFIG["authorized_users"]:
        CONFIG["authorized_users"].append(user_id)
        save_config()
        await update.message.reply_text(f"Usuário <b>{user_id}</b> adicionado à lista de autorizados.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Usuário <b>{user_id}</b> já estava autorizado.", parse_mode="HTML")

async def revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text("Apenas o administrador pode executar este comando.")
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /revoke_user <user_id>")
        return
    try:
        user_id = int(args[1])
    except:
        await update.message.reply_text("ID inválido.")
        return
    if user_id in CONFIG["authorized_users"]:
        CONFIG["authorized_users"].remove(user_id)
        save_config()
        await update.message.reply_text(f"Usuário <b>{user_id}</b> removido da lista de autorizados.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Usuário <b>{user_id}</b> não estava na lista de autorizados.", parse_mode="HTML")

async def grant_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text(f"Apenas o administrador pode executar este comando ({ADMIN_USER}).")
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /grant_group <group_id>")
        return
    try:
        group_id = int(args[1])
    except:
        await update.message.reply_text("ID inválido.")
        return
    if group_id not in CONFIG["authorized_groups"]:
        CONFIG["authorized_groups"].append(group_id)
        save_config()
        await update.message.reply_text(f"Grupo <b>{group_id}</b> adicionado à lista de autorizados.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Grupo <b>{group_id}</b> já estava autorizado.", parse_mode="HTML")

async def revoke_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.username != ADMIN_USER:
        await update.message.reply_text("Apenas o administrador pode executar este comando.")
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /revoke_group <group_id>")
        return
    try:
        group_id = int(args[1])
    except:
        await update.message.reply_text("ID inválido.")
        return
    if group_id in CONFIG["authorized_groups"]:
        CONFIG["authorized_groups"].remove(group_id)
        save_config()
        await update.message.reply_text(f"Grupo <b>{group_id}</b> removido da lista de autorizados.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Grupo <b>{group_id}</b> não estava na lista de autorizados.", parse_mode="HTML")

# ================
# Comandos e Mensagens
# ================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception as e:
        key_content = "Não foi possível ler o arquivo bot_key.pub."
    help_text = (
        "Comandos disponíveis:<br><br>\n\n"
        "<b>/help</b> - Mostrar esta mensagem de ajuda<br>\n"
        "<b>/set_server</b> - Configurar servidor (ip, port e user) (todos os parâmetros são obrigatórios)<br>\n"
        "<b>/server_info</b> - Mostrar informações do servidor configurado para este chat<br>\n"
        "<b>/grant_user &amp; /revoke_user &lt;id&gt;</b> - (ADMIN)<br>\n"
        "<b>/grant_group &amp; /revoke_group &lt;id&gt;</b> - (ADMIN)<br>\n<br>\n"
        "Observações:<br>\n<br>\n"
        "- Em chats privados, todas as mensagens são direcionadas ao bot.<br>\n"
        "- Em grupos, é necessário mencionar o bot (por @ ou com o padrão 'aivis bot') para que ele inicie a interação.<br>\n"
        "- Antes de utilizar o bot (exceto /help), configure o servidor usando <b>/set_server</b>.<br>\n<br>\n"
        "Para gerar uma chave pública em um servidor Linux, execute:<br>\n"
        "  ssh-keygen -t rsa -b 4096 -C \"seu_email@dominio.com\"<br>\n"
        "Depois, use:<br>\n"
        "  cat ~/.ssh/id_rsa.pub<br>\n<br>\n"
        "Esta é a chave pública que o bot usa para autenticação SSH (bot_key.pub):<br>\n<br>\n"
        f"<pre>{key_content}</pre><br>\n<br>\n"
        "Adicione o conteúdo acima ao arquivo <i>~/.ssh/authorized_keys</i> do servidor de destino.<br>\n<br>\n"
        "Você pode contatar o desenvolvedor do bot pelo Telegram: @vivaolinux"
    )
    await update.message.reply_text(sanitize_html(help_text), parse_mode="HTML")
    await turn_on_talking(update, context)

async def enviar_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return
    rep = gerar_relatorio_humanizado()
    logger.info("Enviando relatório solicitado pelo chat %s", update.effective_chat.id)
    logger.info("Conteúdo do relatório:\n%s", rep)
    await update.message.reply_text(sanitize_html(rep), parse_mode="HTML", disable_web_page_preview=True)

def split_into_chunks(text: str, chunk_size: int = 4096) -> list[str]:
    return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)]

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        msg = request_authorization_message(update)
        await update.message.reply_text(sanitize_html(msg))
        return

    # Verifica se o servidor está configurado para este chat
    chat_id = update.effective_chat.id
    cid_str = str(chat_id)
    if cid_str not in CONFIG["servers"] or not all(k in CONFIG["servers"][cid_str] for k in ["ip", "port", "user"]):
        await update.message.reply_text(
            "Servidor não configurado para este chat. Por favor, configure usando:\n"
            "/set_server ip=1.2.3.4 port=22 user=ubuntu\n"
            "E, depois, adicione o conteúdo de bot_key.pub no arquivo ~/.ssh/authorized_keys do servidor de destino."
        )
        return

    original_text = update.message.text
    user = update.effective_user
    uname = user.username
    fullname = user.full_name or user.first_name or "Nome Desconhecido"
    user_msg = f"[{fullname} ({uname})] {original_text}"
    logger.info(f"Recebida mensagem no chat {chat_id}: {user_msg}")

    thread_id = await asyncio.to_thread(find_or_create_thread, chat_id)
    await asyncio.to_thread(send_message_to_thread, thread_id, "user", user_msg)
    run_id = await asyncio.to_thread(run_assistant, thread_id)
    assistant_reply = await asyncio.to_thread(poll_for_response, thread_id, run_id)
    logger.info(f"Resposta assistente chat {chat_id}: {assistant_reply}")

    if "cmd:" in assistant_reply.lower():
        # command = assistant_reply[4:].strip()
        # extract the command after cmd:
        command = assistant_reply.split("cmd:")[1].strip()
        command_output = rodar_comando(chat_id, command)
        prompt = (
            "Segue a resposta do comando. Formate a resposta abaixo de forma concisa e técnica, utilizando html simples (apenas tags i, b, u, s, span, a, code, pre) para envio via Telegram, explicando cada parte:\n\n"
            f"Saída do comando:\n{command_output}"
        )
        await asyncio.to_thread(send_message_to_thread, thread_id, "user", prompt)
        new_run_id = await asyncio.to_thread(run_assistant, thread_id)
        formatted_reply = await asyncio.to_thread(poll_for_response, thread_id, new_run_id)
        for chunk in split_into_chunks(formatted_reply, 4096):
            logger.info(f"Resposta formatada: {chunk}")
            await update.message.reply_text(sanitize_html(chunk), parse_mode="HTML", disable_web_page_preview=True)
        return

    if "#fimdepapo" in assistant_reply.lower():
        DATA["talking"][str(chat_id)] = False
        save_state()
        await update.message.reply_text("Encerrando modo iterativo. Se precisar de ajuda, me mencione novamente ou digite /help")
        return

    for chunk in split_into_chunks(assistant_reply, 4096):
        sanitized_chunk = sanitize_html(chunk)
        logger.info(f"Resposta assistente: {sanitized_chunk}")
        await update.message.reply_text(sanitized_chunk, parse_mode="HTML", disable_web_page_preview=True)

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await talk(update, context)

async def mention_or_regex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()
    await talk(update, context)

async def turn_on_talking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if DATA["talking"].get(str(chat_id), False):
        await talk(update, context)

def main() -> None:
    load_state()
    load_config()
    application = Application.builder().token(BOT_TOKEN).build()

    # Agenda relatório diário
    scheduler = BackgroundScheduler()
    scheduler.add_job(job_enviar_relatorio, "cron", hour=5, minute=7, args=[application])
    scheduler.start()

    # Comandos de admin
    application.add_handler(CommandHandler("grant_user", grant_user))
    application.add_handler(CommandHandler("revoke_user", revoke_user))
    application.add_handler(CommandHandler("grant_group", grant_group))
    application.add_handler(CommandHandler("revoke_group", revoke_group))

    # Comandos de configuração de servidor
    application.add_handler(CommandHandler("set_server", set_server_command))
    application.add_handler(CommandHandler("server_info", server_info_command))

    # Outros comandos
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("relatorio", command_enviar_relatorio))
    application.add_handler(CommandHandler("start", help_command))

    # Handlers para mensagens:
    # Se o chat for privado, todas as mensagens vão para o handler privado.
    private_filter = filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(private_filter, private_message_handler))

    # Em grupos, para iniciar a interação, é necessário mencionar o bot
    mention_filter = filters.Mention(BOT_USERNAME) | filters.Regex(re.compile(r"aivis\s?bot", re.IGNORECASE))
    group_filter = filters.ChatType.GROUPS
    application.add_handler(MessageHandler(mention_filter & group_filter, mention_or_regex_handler))
    # Para mensagens em grupos quando o modo conversa já estiver ativo:
    application.add_handler(MessageHandler(group_filter, handle_any_message))
    
    application.run_polling()

if __name__ == "__main__":
    main()

