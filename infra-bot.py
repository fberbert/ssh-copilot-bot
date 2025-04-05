#!/usr/bin/env python3

import logging
import subprocess
import os
import re
import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

import openai
openai.api_key = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

from telegram import ForceReply, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.background import BackgroundScheduler

# Configurações gerais
TELEGRAM_BOT_TOKEN = os.getenv("AIVIS_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("AIVIS_CHAT_ID")  # Exemplo: "-1001234567890"
STATE_FILE = "bot_state.json"  # arquivo JSON para salvar/restaurar threads e estado de conversa
SERVER_IP = os.getenv("SERVER_IP")  # IP do servidor para executar comandos
SERVER_PORT = os.getenv("SERVER_PORT")  # Porta do servidor para executar comandos
SERVER_USER = os.getenv("SERVER_USER")  # Usuário do servidor para executar comandos

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Estrutura de dados em memória (estado), será sincronizada com o JSON.
DATA = {
    "threads": {},   # chat_id -> thread_id
    "talking": {}    # chat_id -> bool (se está conversando ou não)
}

def load_state():
    """Carrega o estado do arquivo JSON (se existir) e atualiza DATA."""
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
    """Salva o dicionário DATA no arquivo JSON."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, indent=2)
        logger.info("Estado salvo em %s", STATE_FILE)
    except Exception as e:
        logger.error("Erro ao salvar estado em %s: %s", STATE_FILE, e)

def rodar_comando(comando):
    try:
        # Se comando possuir ', substitui por \'
        if "'" in comando:
            comando = comando.replace("'", "\\'")

        # Executa o comando no servidor remoto via SSH, a partir do terminal deste servidor
        comando = f"ssh {SERVER_USER}@{SERVER_IP} -p {SERVER_PORT} '{comando}'"

        saida = subprocess.check_output(comando, shell=True, text=True)
        return saida.strip()
    except subprocess.CalledProcessError as e:
        return f"Erro ao executar '{comando}': {e}"

# Relatório padrão (não humanizado)
def gerar_relatorio():
    saida_df = rodar_comando("df -h")
    saida_backups = rodar_comando("/home/fabio/bin/listar-backups")
    saida_snapshots = rodar_comando("/home/fabio/bin/listar-snapshots")
    mensagem = (
        "Relatório diário:\n\n"
        f"--- df -h ---\n{saida_df}\n\n"
        f"--- listar-backups ---\n{saida_backups}\n\n"
        f"--- listar-snapshots ---\n{saida_snapshots}"
    )
    return mensagem

# Relatório humanizado: executa 5 comandos e envia o prompt para o assistente formatar a resposta
def gerar_relatorio_humanizado():
    output_df = rodar_comando("df -h")
    output_backups = rodar_comando("/home/fabio/bin/listar-backups")
    output_snapshots = rodar_comando("/home/fabio/bin/listar-snapshots")
    output_apache = rodar_comando("service apache2 status")
    output_mysql = rodar_comando("service mysql status")
    
    prompt = (
        "Você é um assistente de TI de suporte à infraestrutura do sistema Aivis. "
        "Abaixo estão as saídas dos comandos referentes a diferentes aspectos do servidor. "
        "Formate essas informações de forma concisa e técnica, considerando que está conversando com profissionais de TI, "
        "sem explicações óbvias. Utilize um formato claro e direto para informar a situação de cada serviço.\n\n"
        "Espaço em disco do servidor (df -h):\n" + output_df + "\n\n"
        "Últimos 21 backups do banco de dados no AWS S3 (listar-backups):\n" + output_backups + "\n\n"
        "Último snapshot da instância EC2 (listar-snapshots):\n" + output_snapshots + "\n\n"
        "Status do serviço apache2 (service apache2 status):\n" + output_apache + "\n\n"
        "Status do serviço mysql (service mysql status):\n" + output_mysql
    )
    # Utiliza a thread associada ao TELEGRAM_CHAT_ID para manter o histórico
    chat_id = int(TELEGRAM_CHAT_ID)
    thread_id = find_or_create_thread(chat_id)
    send_message_to_thread(thread_id, "user", prompt)
    run_id = run_assistant(thread_id)
    formatted_report = poll_for_response(thread_id, run_id)
    return formatted_report

def job_enviar_relatorio(app):
    report = gerar_relatorio_humanizado()
    app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode="Markdown")

# =======================
# Funções de integração com a API de Threads da OpenAI
# =======================

def find_or_create_thread(chat_id: int) -> str:
    """
    Verifica se já existe uma thread associada ao chat_id.
    Se não existir, cria uma nova thread e armazena em DATA["threads"].
    """
    cid_str = str(chat_id)
    if cid_str in DATA["threads"]:
        return DATA["threads"][cid_str]
    
    response = openai.beta.threads.create()
    thread_id = response.id
    DATA["threads"][cid_str] = thread_id
    logger.info(f"Criada nova thread para chat {chat_id}: {thread_id}")
    save_state()
    return thread_id

def send_message_to_thread(thread_id, role, content):
    response = openai.beta.threads.messages.create(
        thread_id=thread_id,
        role=role,
        content=content
    )
    logger.info(f"Mensagem criada: {response}")
    return response

def run_assistant(thread_id):
    run_response = openai.beta.threads.runs.create(thread_id, assistant_id=ASSISTANT_ID)
    return run_response.id

def poll_for_response(thread_id, run_id, timeout=30):
    import time
    start_time = time.time()
    while time.time() - start_time < timeout:
        run_status = openai.beta.threads.runs.retrieve(run_id=run_id, thread_id=thread_id)
        if run_status.status == "completed":
            messages = openai.beta.threads.messages.list(thread_id=thread_id)
            sorted_messages = sorted(messages.data, key=lambda m: m.created_at, reverse=True)
            for message in sorted_messages:
                if message.role == "assistant":
                    text_blocks = []
                    for block in message.content:
                        if block.type == "text":
                            text_blocks.append(block.text.value)
                    content_str = "\n".join(text_blocks)
                    return content_str
        time.sleep(2)
    return "Timeout: não foi possível obter resposta"

# =======================
# Lógica de conversa
# =======================

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Recebe a mensagem do usuário, envia à thread e obtém a resposta do assistente.
    Se a resposta iniciar com "cmd:", extrai o comando, executa-o no servidor,
    então cria uma nova mensagem na thread com um prompt que solicita ao assistente
    que formate a saída do comando de forma concisa para profissionais de TI.
    A resposta dessa nova execução será enviada ao Telegram.
    Se a resposta contiver "#fimdepapo", desativa o modo de conversa.
    """
    chat_id = update.effective_chat.id
    original_text = update.message.text

    user = update.effective_user
    user_name = user.username 
    user_full_name = user.full_name or user.first_name or "Nome Desconhecido"
    user_message = f"[{user_full_name} ({user_name})] {original_text}"

    logger.info(f"Recebida mensagem no chat {chat_id}: {user_message}")
    
    thread_id = await asyncio.to_thread(find_or_create_thread, chat_id)
    await asyncio.to_thread(send_message_to_thread, thread_id, "user", user_message)
    run_id = await asyncio.to_thread(run_assistant, thread_id)
    assistant_reply = await asyncio.to_thread(poll_for_response, thread_id, run_id)
    logger.info(f"Resposta do assistente no chat {chat_id}: {assistant_reply}")

    if assistant_reply.lower().startswith("cmd:"):
        command = assistant_reply[4:].strip()
        command_output = rodar_comando(command)
        logger.info(f"Saída do comando '{command}': {command_output}")

        prompt = (
            "Você é um assistente de TI de suporte à infraestrutura do sistema Aivis. "
            "Converse com profissionais de TI, sem explicações óbvias. "
            "Formate a resposta abaixo de forma concisa e técnica, explicando a situação:\n\n"
            f"Saída do comando:\n{command_output}"
        )
        
        await asyncio.to_thread(send_message_to_thread, thread_id, "user", prompt)
        new_run_id = await asyncio.to_thread(run_assistant, thread_id)
        formatted_reply = await asyncio.to_thread(poll_for_response, thread_id, new_run_id)
        await context.bot.send_message(chat_id=chat_id, text=formatted_reply, parse_mode="Markdown")
        return

    if "#fimdepapo" in assistant_reply.lower():
        DATA["talking"][str(chat_id)] = False
        save_state()
        await context.bot.send_message(chat_id=chat_id, text="Se precisar de ajuda, me mencione novamente!")
        return

    await context.bot.send_message(chat_id=chat_id, text=assistant_reply, parse_mode="Markdown")

# =======================
# Handlers do Telegram
# =======================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Comandos disponíveis:\n"
        "/help - Mostrar esta mensagem de ajuda\n"
        "/relatorio - Enviar relatório diário humanizado\n\n"
        "O bot entra em modo de conversa sempre que for mencionado (por @ ou regex). "
    )

async def enviar_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    report = gerar_relatorio_humanizado()
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode="Markdown")

async def mention_or_regex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    DATA["talking"][str(chat_id)] = True
    save_state()
    await talk(update, context)

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    is_talking = DATA["talking"].get(str(chat_id), False)
    if is_talking:
        await talk(update, context)
    else:
        return

def main() -> None:
    load_state()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    group_filter = filters.Chat(chat_id=int(TELEGRAM_CHAT_ID))
    user_filter = filters.User(user_id=99952935)  # Exemplo de restrição opcional
    allowed_user_or_group = user_filter | group_filter

    application.add_handler(CommandHandler("help", help_command, filters=allowed_user_or_group))
    application.add_handler(CommandHandler("relatorio", enviar_relatorio, filters=allowed_user_or_group))

    mention_filter = filters.Mention("@aivisinfra_bot") | filters.Regex(re.compile(r"aivis\s?bot", re.IGNORECASE))
    application.add_handler(MessageHandler(mention_filter & allowed_user_or_group, mention_or_regex_handler))
    application.add_handler(MessageHandler(allowed_user_or_group, handle_any_message))

    scheduler = BackgroundScheduler()
    scheduler.add_job(job_enviar_relatorio, "cron", hour=5, minute=7, args=[application])
    scheduler.start()

    application.run_polling()

if __name__ == "__main__":
    main()
