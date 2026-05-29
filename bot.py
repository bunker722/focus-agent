import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import anthropic
import telebot
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

FEATURE_FREEZE_UNTIL = "2025-08-01"
MAX_ACTIVE_VECTORS = 2
COOLDOWN_THRESHOLD = 3

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = Path("system_prompt.txt").read_text(encoding="utf-8")

# ── Clients ───────────────────────────────────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect("focus.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            status TEXT,
            count INTEGER DEFAULT 1,
            last_seen TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT,
            created_at TEXT
        )
    """)
    con.commit()
    con.close()

def get_idea_count(text: str) -> int:
    con = sqlite3.connect("focus.db")
    keywords = text.lower().split()[:4]
    query = "SELECT count FROM ideas WHERE " + " OR ".join(["lower(text) LIKE ?" for _ in keywords])
    params = [f"%{k}%" for k in keywords]
    row = con.execute(query, params).fetchone()
    con.close()
    return row[0] if row else 0

def upsert_idea(text: str, status: str):
    con = sqlite3.connect("focus.db")
    keywords = text.lower().split()[:4]
    query = "SELECT id, count FROM ideas WHERE " + " OR ".join(["lower(text) LIKE ?" for _ in keywords])
    params = [f"%{k}%" for k in keywords]
    row = con.execute(query, params).fetchone()
    now = datetime.now().isoformat()
    if row:
        con.execute("UPDATE ideas SET count=?, status=?, last_seen=? WHERE id=?",
                    (row[1] + 1, status, now, row[0]))
    else:
        con.execute("INSERT INTO ideas (text, status, last_seen) VALUES (?, ?, ?)",
                    (text[:200], status, now))
    con.commit()
    con.close()

def get_last_output_days() -> int:
    con = sqlite3.connect("focus.db")
    row = con.execute("SELECT created_at FROM outputs ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row:
        return 999
    delta = datetime.now() - datetime.fromisoformat(row[0])
    return delta.days

def log_output(description: str):
    con = sqlite3.connect("focus.db")
    con.execute("INSERT INTO outputs (description, created_at) VALUES (?, ?)",
                (description[:300], datetime.now().isoformat()))
    con.commit()
    con.close()

# ── TTS ───────────────────────────────────────────────────────────────────────
def text_to_voice(text: str) -> bytes:
    response = openai_client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text[:4000]
    )
    return response.content

# ── Claude ────────────────────────────────────────────────────────────────────
def ask_claude(user_message: str, extra_context: str = "") -> str:
    full_message = user_message
    if extra_context:
        full_message = f"{extra_context}\n\nСообщение пользователя: {user_message}"

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": full_message}]
    )
    return response.content[0].text

# ── Bot handlers ──────────────────────────────────────────────────────────────
def is_allowed(message) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return message.from_user.id == ALLOWED_USER_ID

def send_voice_reply(chat_id: int, text: str):
    try:
        audio = text_to_voice(text)
        bot.send_voice(chat_id, audio)
    except Exception as e:
        logging.error(f"TTS error: {e}")
        bot.send_message(chat_id, text)

@bot.message_handler(commands=["start"])
def handle_start(message):
    if not is_allowed(message):
        return
    bot.send_message(message.chat.id,
        "Focus Agent активен.\n\nПиши идеи, проекты, импульсы.\nОтвечу голосом.\n\nКоманды:\n/output — зафиксировать реальный результат\n/status — сколько дней без output")

@bot.message_handler(commands=["output"])
def handle_output(message):
    if not is_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Напиши что сделал: /output опубликовал видео про X")
        return
    log_output(parts[1])
    send_voice_reply(message.chat.id, f"Зафиксировано. Это реальный output. Продолжай.")

@bot.message_handler(commands=["status"])
def handle_status(message):
    if not is_allowed(message):
        return
    days = get_last_output_days()
    if days == 999:
        text = "Реального output ещё не было. Что выйдет первым?"
    elif days == 0:
        text = "Output сегодня. Хорошо."
    else:
        text = f"Последний output {days} дн. назад. Что мешает закрыть цикл сегодня?"
    send_voice_reply(message.chat.id, text)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_idea(message):
    if not is_allowed(message):
        return

    text = message.text.strip()

    # Cooldown check
    count = get_idea_count(text)
    if count >= COOLDOWN_THRESHOLD:
        reply = f"Эта идея уже была заморожена {count} раз. REJECT."
        send_voice_reply(message.chat.id, reply)
        return

    # Build context
    days_since_output = get_last_output_days()
    context = f"Дней без реального output: {days_since_output}."
    if count > 0:
        context += f" Похожая идея уже появлялась {count} раз."

    # Ask Claude
    reply = ask_claude(text, context)

    # Parse status and save
    status = "FREEZE"
    if "ACCEPT" in reply:
        status = "ACCEPT"
    elif "REJECT" in reply:
        status = "REJECT"
    upsert_idea(text, status)

    send_voice_reply(message.chat.id, reply)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    logging.info("Focus Agent started")
    bot.infinity_polling()
