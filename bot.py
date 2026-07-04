import os
import sqlite3
import logging
from datetime import datetime, time

import pytz
import dateparser
from dotenv import load_dotenv
from google import genai

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_TELEGRAM_ID"])
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Jerusalem")
DB_PATH = "agent.db"

tz = pytz.timezone(TIMEZONE)
client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("agent")

SYSTEM_PROMPT = (
    "You are a helpful personal assistant for one user via Telegram. "
    "Always reply in the same language the user writes in. "
    "If the user writes Hebrew, reply in natural Hebrew. "
    "If the user writes English, reply in English. "
    "Be concise."
)

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            due_at TEXT,
            done INTEGER DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            created_at TEXT,
            done INTEGER DEFAULT 0
        )
    """)
    con.commit()
    con.close()

def add_reminder(text, due_at):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO reminders(text, due_at) VALUES (?, ?)", (text, due_at.isoformat()))
    con.commit()
    con.close()

def get_due_reminders():
    con = sqlite3.connect(DB_PATH)
    now = datetime.now(tz).isoformat()
    rows = con.execute(
        "SELECT id, text FROM reminders WHERE due_at <= ? AND done=0",
        (now,)
    ).fetchall()
    con.close()
    return rows

def mark_reminder_done(rid):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
    con.commit()
    con.close()

def add_task(text):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO tasks(text, created_at) VALUES (?, ?)",
        (text, datetime.now(tz).isoformat())
    )
    con.commit()
    con.close()

def get_open_tasks():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT id, text FROM tasks WHERE done=0").fetchall()
    con.close()
    return rows

def complete_task(task_id):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,))
    con.commit()
    con.close()

def authorized(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)

def ask_gemini(user_message: str) -> str:
    prompt = SYSTEM_PROMPT + "\n\nUser: " + user_message
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return resp.text or "I couldn't generate a reply."

def parse_reminder_time(text: str):
    dt = dateparser.parse(
        text,
        languages=["he", "en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": TIMEZONE,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not dt:
        return None, None
    return dt, text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Hi. אני כאן.\n"
        "/remind <when> <what>\n"
        "/task <text>\n"
        "/tasks\n"
        "/done <id>\n"
        "/summary\n"
        "Or just write normally."
    )

async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /remind tomorrow at 5pm call mom")
        return
    dt, content = parse_reminder_time(text)
    if not dt:
        await update.message.reply_text("Could not understand the time.")
        return
    add_reminder(content, dt)
    await update.message.reply_text(f"✅ Reminder set for {dt.strftime('%Y-%m-%d %H:%M')}\n{content}")

async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /task Buy groceries")
        return
    add_task(text)
    await update.message.reply_text(f"📝 Task added: {text}")

async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    rows = get_open_tasks()
    if not rows:
        await update.message.reply_text("No open tasks.")
        return
    msg = "\n".join([f"{r[0]}. {r[1]}" for r in rows])
    await update.message.reply_text("Your open tasks:\n" + msg)

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /done <task_id>")
        return
    try:
        task_id = int(context.args[0])
        complete_task(task_id)
        await update.message.reply_text(f"✅ Task {task_id} marked done.")
    except ValueError:
        await update.message.reply_text("Invalid task ID.")

async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await send_daily_summary(context.application, update.effective_chat.id)

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.chat.send_action("typing")
    reply = ask_gemini(update.message.text)
    await update.message.reply_text(reply)

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    due = get_due_reminders()
    for rid, text in due:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⏰ Reminder: {text}")
            mark_reminder_done(rid)
        except Exception as e:
            log.error(f"Reminder error: {e}")

async def send_daily_summary(app, chat_id=None):
    chat_id = chat_id or OWNER_ID
    tasks = get_open_tasks()
    task_text = "\n".join([f"- {t[1]}" for t in tasks]) if tasks else "No open tasks."
    msg = f"☀️ Good morning!\n\nTasks:\n{task_text}"
    await app.bot.send_message(chat_id=chat_id, text=msg)

async def daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
    await send_daily_summary(context.application)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("task", task_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    app.job_queue.run_repeating(check_reminders, interval=60, first=10)
    app.job_queue.run_daily(daily_summary_job, time=time(10, 0))

    log.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
