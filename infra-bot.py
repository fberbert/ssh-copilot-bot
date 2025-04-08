#!/usr/bin/env python3

import logging
import subprocess
import os
import re
import asyncio
import json
import bleach
import asyncssh
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
KEY_FILE = os.path.join(PROJECT_DIR, "bot_key.pub")   # Arquivo da chave pública do bot a ser usada no SSH
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
    "servers": {}  # str(chat_id) -> { "selected_server": str, "servers": { serverName -> { ip, port, user } } }
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
# Funções de sanitização e divisão de texto
# ================
def sanitize_html(text: str) -> str:
    # Converte <br> em nova linha
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Converte <p> e </p> em nova linha
    text = re.sub(r'\s*<\s*/?\s*p\s*>\s*', '\n', text, flags=re.IGNORECASE)
    # Remove tags <span> e </span>
    text = re.sub(r'<span[^>]*?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</span>', '', text, flags=re.IGNORECASE)
    # Remove <pre><code ...> e </code></pre> se ambos existirem
    if re.search(r'<pre><code[^>]*?>', text, flags=re.IGNORECASE) and re.search(r'</code></pre>', text, flags=re.IGNORECASE):
        text = re.sub(r'<pre><code[^>]*?>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</code></pre>', '', text, flags=re.IGNORECASE)
    
    # Permitir somente algumas tags
    allowed_tags = ['b', 'i', 'code', 'pre', 'a']
    text = bleach.clean(text, tags=allowed_tags, strip=True)

    return text

def split_into_chunks(text: str, chunk_size: int = 4096) -> list[str]:
    return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)]

# ================
# OpenAI: criação e manipulação de threads e mensagens
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
# SSH e execução de comandos
# ================
async def async_rodar_comando(chat_id: int, comando: str) -> str:
    cid_str = str(chat_id)

    # Verifica se há registro para este chat
    if cid_str not in CONFIG["servers"]:
        return (
            "Nenhum servidor configurado para este chat.\n"
            "Por favor, configure um servidor usando:\n"
            "/set_server ip=1.2.3.4 port=22 user=ubuntu name=NomeDoServidor\n"
            "Depois, adicione o conteúdo de bot_key.pub no arquivo ~/.ssh/authorized_keys do servidor."
        )
    # Verifica se existe algum servidor selecionado
    selected_server = CONFIG["servers"][cid_str].get("selected_server")
    if not selected_server:
        return (
            "Nenhum servidor está selecionado para este chat.\n"
            "Use /select_server <NomeDoServidor> ou configure um novo servidor com /set_server."
        )

    # Busca dados do servidor selecionado
    servers_dict = CONFIG["servers"][cid_str].get("servers", {})
    if selected_server not in servers_dict:
        return (
            f"O servidor '{selected_server}' não existe mais ou não foi configurado corretamente.\n"
            "Use /list_servers para verificar."
        )

    server_info = servers_dict[selected_server]
    ip = server_info["ip"]
    port = int(server_info["port"])
    user = server_info["user"]

    # Escape de aspas simples
    safe_cmd = comando.replace("'", "'\"'\"'")
    logger.info(f"Executando comando '{comando}' no servidor {ip} ({user}@{ip}:{port}) via AsyncSSH")
    try:
        async with asyncssh.connect(ip, port=port, username=user, known_hosts=None) as conn:
            result = await conn.run(safe_cmd, check=True)
            return result.stdout.strip()
    except Exception as e:
        return f"Erro ao executar comando via AsyncSSH: {e}"

# ================
# Relatórios
# ================
async def gerar_relatorio_humanizado(cid) -> str:
    # Aqui estamos assumindo que o REPORT_CHAT_ID terá um servidor configurado e selecionado
    # cid = int(REPORT_CHAT_ID)
    output_df = await async_rodar_comando(cid, "df -h")
    output_backups = await async_rodar_comando(cid, "/home/fabio/bin/listar-backups")
    output_snaps = await async_rodar_comando(cid, "/home/fabio/bin/listar-snapshots")
    output_apache = await async_rodar_comando(cid, "service apache2 status")
    output_mysql = await async_rodar_comando(cid, "service mysql status")

    prompt = (
        "Abaixo estão as saídas dos comandos referentes a diferentes aspectos do servidor. "
        "Formate essas informações de forma concisa e técnica, utilizando HTML simples para envio via Telegram:<br><br>"
        "<b>Espaço em disco (df -h):</b><br>" + output_df + "<br><br>" +
        "<b>Backups no AWS S3:</b><br>" + output_backups + "<br><br>" +
        "<b>Snapshot EC2:</b><br>" + output_snaps + "<br><br>" +
        "<b>Apache2 status:</b><br>" + output_apache + "<br><br>" +
        "<b>MySQL status:</b><br>" + output_mysql
    )
    thread_id = find_or_create_thread(cid)
    send_message_to_thread(thread_id, "user", prompt)
    run_id = run_assistant(thread_id)
    answer = poll_for_response(thread_id, run_id)
    return answer

def job_enviar_relatorio(app):
    text = gerar_relatorio_humanizado(REPORT_CHAT_ID)
    logger.info("Enviando relatório diário para o chat %s", REPORT_CHAT_ID)
    logger.info("Conteúdo do relatório:\n%s", text)
    app.bot.send_message(chat_id=REPORT_CHAT_ID, text=text, parse_mode="HTML")

async def command_enviar_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await asyncio.to_thread(gerar_relatorio_humanizado(update.effective_chat.id))
    logger.info("Enviando relatório diário para o chat %s", update.effective_chat.id)
    logger.info("Conteúdo do relatório:\n%s", text)
    await update.message.reply_text(sanitize_html(text), parse_mode="HTML", disable_web_page_preview=True)
    await turn_on_talking(update, context)

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
        f"Você não está autorizado(a) a usar este bot.\n"
        f"Solicite autorização a {ADMIN_USER}, informando o ID: {chat_id}"
    )

# ================
# Comandos para gerenciar servidores
# ================
async def set_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Configura um novo servidor para este chat.
    Exemplo de uso: /set_server ip=1.2.3.4 port=22 user=ubuntu name=NomeServidor
    Após configurar, o bot exibirá a chave pública para ser adicionada em ~/.ssh/authorized_keys no servidor.
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

    # Verifica parâmetros obrigatórios
    if not all(k in server_data for k in ["ip", "port", "user", "name"]):
        await update.message.reply_text(
            "Todos os parâmetros são obrigatórios:\n"
            "Use: /set_server ip=1.2.3.4 port=22 user=ubuntu name=NomeServidor"
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

    # Toda vez que um novo servidor é adicionado, torna-se o servidor selecionado
    CONFIG["servers"][cid_str]["selected_server"] = server_name

    save_config()
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception as e:
        key_content = "Não foi possível ler o arquivo bot_key.pub."

    reply = (
        f"Servidor <b>{server_name}</b> configurado com sucesso e agora está selecionado.\n\n"
        "Para permitir SSH sem senha, adicione a chave abaixo ao arquivo <i>~/.ssh/authorized_keys</i> "
        "no servidor de destino:\n\n"
        f"<pre>{key_content}</pre>"
    )
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def list_servers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lista todos os servidores configurados para este chat.
    Mostra um indicador ao lado do servidor que está selecionado atualmente.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    if cid_str not in CONFIG["servers"] or not CONFIG["servers"][cid_str].get("servers"):
        await update.message.reply_text("Nenhum servidor configurado para este chat.")
        return

    selected_server = CONFIG["servers"][cid_str].get("selected_server")
    servers = CONFIG["servers"][cid_str]["servers"]
    reply_lines = ["Servidores configurados para este chat:"]
    for name, info in servers.items():
        if name == selected_server:
            reply_lines.append(f"- <b>{name}</b> (selecionado): {info.get('ip')}:{info.get('port')} ({info.get('user')})")
        else:
            reply_lines.append(f"- <b>{name}</b>: {info.get('ip')}:{info.get('port')} ({info.get('user')})")

    reply = "\n".join(reply_lines)
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def server_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Exibe informações detalhadas sobre um servidor específico, se o nome for informado.
    Se não for informado, lista todos os servidores configurados.
    Uso: /server_info [NomeServidor]
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
                f"Informações do servidor <b>{server_name}</b>:\n"
                f"IP: <b>{info.get('ip', 'N/A')}</b>\n"
                f"Porta: <b>{info.get('port', 'N/A')}</b>\n"
                f"Usuário: <b>{info.get('user', 'N/A')}</b>"
            )
        else:
            reply = f"Nenhum servidor encontrado com o nome <b>{server_name}</b>."
    else:
        # Se não houver argumento, lista os servidores
        if cid_str in CONFIG["servers"] and CONFIG["servers"][cid_str].get("servers"):
            servers = CONFIG["servers"][cid_str]["servers"]
            selected_server = CONFIG["servers"][cid_str].get("selected_server")
            lines = ["Servidores configurados neste chat:"]
            for name, info in servers.items():
                if name == selected_server:
                    lines.append(f"- <b>{name}</b> (selecionado): {info['ip']}:{info['port']} ({info['user']})")
                else:
                    lines.append(f"- <b>{name}</b>: {info['ip']}:{info['port']} ({info['user']})")
            reply = "\n".join(lines)
        else:
            reply = "Nenhum servidor configurado para este chat."
    await update.message.reply_text(sanitize_html(reply), parse_mode="HTML")


async def edit_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Edita a configuração de um servidor existente.
    Uso: /edit_server NomeServidor ip=... port=... user=...
    Pelo menos um parâmetro (ip, port ou user) deve ser fornecido.
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 3:
        await update.message.reply_text("Uso: /edit_server <NomeServidor> ip=... port=... user=...")
        return

    server_name = args[1]
    if (
        cid_str not in CONFIG["servers"]
        or not CONFIG["servers"][cid_str].get("servers")
        or server_name not in CONFIG["servers"][cid_str]["servers"]
    ):
        await update.message.reply_text(f"Servidor '{server_name}' não encontrado.")
        return

    server_data = {}
    for p in args[2:]:
        if "=" in p:
            k, v = p.split("=", 1)
            server_data[k.strip().lower()] = v.strip()

    if not any(k in server_data for k in ["ip", "port", "user"]):
        await update.message.reply_text("Informe pelo menos um parâmetro para atualizar (ip, port ou user).")
        return

    CONFIG["servers"][cid_str]["servers"][server_name].update(server_data)
    save_config()
    await update.message.reply_text(f"Servidor '{server_name}' atualizado com sucesso.", parse_mode="HTML")


async def delete_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Exclui a configuração de um servidor.
    Uso: /delete_server NomeServidor
    Se o servidor for o selecionado, ele será removido e o selected_server passará a ser None
    (ou outro servidor, se existir).
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /delete_server <NomeServidor>")
        return

    server_name = args[1]
    if (
        cid_str in CONFIG["servers"]
        and CONFIG["servers"][cid_str].get("servers")
        and server_name in CONFIG["servers"][cid_str]["servers"]
    ):
        # Se for o servidor selecionado, remover seleção
        if CONFIG["servers"][cid_str].get("selected_server") == server_name:
            CONFIG["servers"][cid_str]["selected_server"] = None

        del CONFIG["servers"][cid_str]["servers"][server_name]
        # Se ainda existirem servidores, seleciona o primeiro (arbitrário) como padrão
        if CONFIG["servers"][cid_str]["servers"]:
            some_server = list(CONFIG["servers"][cid_str]["servers"].keys())[0]
            CONFIG["servers"][cid_str]["selected_server"] = some_server

        save_config()
        await update.message.reply_text(f"Servidor '{server_name}' excluído com sucesso.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Servidor '{server_name}' não encontrado.")

async def select_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Seleciona qual servidor será usado para este chat.
    Uso: /select_server NomeServidor
    """
    chat_id = update.effective_chat.id
    if not is_authorized(update):
        await update.message.reply_text(sanitize_html(request_authorization_message(update)))
        return

    cid_str = str(chat_id)
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Uso: /select_server <NomeServidor>")
        return

    server_name = args[1]
    if (
        cid_str not in CONFIG["servers"]
        or not CONFIG["servers"][cid_str].get("servers")
        or server_name not in CONFIG["servers"][cid_str]["servers"]
    ):
        await update.message.reply_text(f"Servidor '{server_name}' não encontrado. Use /list_servers para verificar.")
        return

    CONFIG["servers"][cid_str]["selected_server"] = server_name
    save_config()
    await update.message.reply_text(f"Servidor '{server_name}' agora está selecionado.", parse_mode="HTML")


# ================
# Comandos de autorização (ADMIN)
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
# Comandos gerais
# ================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Exibe mensagem de ajuda.
    """
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key_content = f.read().strip()
    except Exception as e:
        key_content = "Não foi possível ler o arquivo bot_key.pub."
    
    help_text = (
        "Comandos disponíveis:<br><br>\n"
        "<b>/help</b> - Exibe esta mensagem de ajuda<br>\n"
        "<b>/set_server</b> - Configura um novo servidor para este chat. Exemplo:<br>\n"
        " <code>/set_server ip=1.2.3.4 port=22 user=ubuntu name=NomeServidor</code><br>\n"
        "<b>/list_servers</b> - Lista todos os servidores configurados para este chat.<br>\n"
        "<b>/select_server NomeServidor</b> - Seleciona qual servidor será usado nas execuções de comando.<br>\n"
        "<b>/server_info [NomeServidor]</b> - Mostra detalhes de um servidor ou lista todos se não passar nome.<br>\n"
        "<b>/edit_server NomeServidor ip=... port=... user=...</b> - Edita a configuração de um servidor.<br>\n"
        "<b>/delete_server NomeServidor</b> - Exclui um servidor.<br>\n"
        "<b>/grant_user</b> / <b>/revoke_user</b> &lt;id&gt; - (Apenas ADMIN)<br>\n"
        "<b>/grant_group</b> / <b>/revoke_group</b> &lt;id&gt; - (Apenas ADMIN)<br>\n"
        "<b>/relatorio</b> - Gera um relatório diário do servidor selecionado.<br><br>\n"
        "Observações:<br><br>\n"
        "- Em chats privados, todas as mensagens são interpretadas pelo bot.<br>\n"
        "- Em grupos, mencione o bot (@BotUsername) ou use a expressão 'aivis bot' para ativar o diálogo.<br>\n"
        "- Antes de usar o bot (exceto /help), configure pelo menos um servidor com <b>/set_server</b>.<br><br>\n"
        "Esta é a chave pública do bot (<i>bot_key.pub</i>):<br><br>\n"
        f"<pre>{key_content}</pre><br>\n"
        "Adicione a chave acima em <i>~/.ssh/authorized_keys</i> no servidor que deseja gerenciar.<br><br>\n"
        "Em caso de dúvidas, contate o desenvolvedor do bot no Telegram: @vivaolinux"
    )
    await update.message.reply_text(sanitize_html(help_text), parse_mode="HTML")
    await turn_on_talking(update, context)

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Trata o diálogo principal com o usuário, incluindo a interação com o ChatGPT.
    Se houver a palavra "cmd:" na resposta do assistente, executa o comando no servidor selecionado.
    """
    if not is_authorized(update):
        msg = request_authorization_message(update)
        await update.message.reply_text(sanitize_html(msg))
        return

    chat_id = update.effective_chat.id
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
    logger.info(f"Resposta da IA (chat {chat_id}): {assistant_reply}")

    # Se a resposta contiver "cmd:", significa que o bot solicitou executar algum comando via SSH
    if "cmd:" in assistant_reply.lower():
        command = assistant_reply.split("cmd:")[1].strip()
        command_output = await async_rodar_comando(chat_id, command)
        prompt = (
            "Segue a resposta do comando. Formate a resposta abaixo de forma concisa e técnica, "
            "usando HTML simples (apenas tags i, b, code, pre, a) para envio via Telegram, "
            "explicando o resultado:\n\n"
            "Saída do comando:\n" + command_output
        )
        await asyncio.to_thread(send_message_to_thread, thread_id, "user", prompt)
        new_run_id = await asyncio.to_thread(run_assistant, thread_id)
        formatted_reply = await asyncio.to_thread(poll_for_response, thread_id, new_run_id)
        for chunk in split_into_chunks(formatted_reply, 4096):
            await update.message.reply_text(sanitize_html(chunk), parse_mode="HTML", disable_web_page_preview=True)
        return

    # Se a resposta contiver "#fimdepapo", encerramos o modo conversa
    if "#fimdepapo" in assistant_reply.lower():
        DATA["talking"][str(chat_id)] = False
        save_state()
        await update.message.reply_text("Encerrando modo iterativo. Se precisar de algo, me mencione ou use /help.")
        return

    # Caso contrário, apenas exibe a resposta
    for chunk in split_into_chunks(assistant_reply, 4096):
        sanitized_chunk = sanitize_html(chunk)
        await update.message.reply_text(sanitized_chunk, parse_mode="HTML", disable_web_page_preview=True)

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler para mensagens em chats privados. Tudo que o usuário digitar irá para a função talk().
    """
    await talk(update, context)

async def mention_or_regex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler para mensagens em grupos que mencionam o bot ou contém o padrão 'aivis bot'.
    Ativa o modo de conversa e chama a função talk().
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
    Liga o modo de conversa para o chat.
    """
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler genérico para grupos. Se o chat está em modo de conversa, envia para talk().
    """
    chat_id = update.effective_chat.id
    if DATA["talking"].get(str(chat_id), False):
        await talk(update, context)

# ================
# Função principal
# ================
def main() -> None:
    load_state()
    load_config()
    application = Application.builder().token(BOT_TOKEN).build()

    # Agendando relatório diário (05:07 AM)
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
    application.add_handler(CommandHandler("list_servers", list_servers_command))
    application.add_handler(CommandHandler("edit_server", edit_server_command))
    application.add_handler(CommandHandler("delete_server", delete_server_command))
    application.add_handler(CommandHandler("select_server", select_server_command))

    # Outros comandos
    application.add_handler(CommandHandler("server_info", server_info_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("relatorio", command_enviar_relatorio))
    application.add_handler(CommandHandler("start", help_command))

    # Handlers para mensagens:
    # 1) Chats privados: qualquer mensagem cai em private_message_handler
    private_filter = filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(private_filter, private_message_handler))

    # 2) Em grupos, para iniciar a interação, é necessário mencionar o bot (@BotUsername ou 'aivis bot')
    mention_filter = filters.Mention(BOT_USERNAME) | filters.Regex(re.compile(r"aivis\s?bot", re.IGNORECASE))
    group_filter = filters.ChatType.GROUPS
    application.add_handler(MessageHandler(mention_filter & group_filter, mention_or_regex_handler))

    # 3) Para mensagens em grupos após o modo conversa estar ativo:
    application.add_handler(MessageHandler(group_filter, handle_any_message))
    
    application.run_polling()

if __name__ == "__main__":
    main()

