import os
import logging
from typing import Final

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("agent")

BOT_TOKEN: Final[str | None] = os.getenv("TELEGRAM_BOT_TOKEN")
NARAROUTER_API_KEY: Final[str | None] = os.getenv("NARAROUTER_API_KEY")
NARAROUTER_BASE_URL: Final[str] = os.getenv("NARAROUTER_BASE_URL", "https://router.bynara.id/v1")
NARAROUTER_MODEL: Final[str] = os.getenv("NARAROUTER_MODEL", "openai/gpt-4o-mini")
SYSTEM_PROMPT: Final[str] = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful Telegram personal assistant. Reply clearly and briefly.",
)

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

if not NARAROUTER_API_KEY:
    raise RuntimeError("Missing NARAROUTER_API_KEY environment variable")

client = OpenAI(
    api_key=NARAROUTER_API_KEY,
    base_url=NARAROUTER_BASE_URL,
    max_retries=2,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Bot is up. Send me a message.")


async def ask_model(user_text: str) -> str:
    try:
        response = client.chat.completions.create(
            model=NARAROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
        )
        content = response.choices[0].message.content
        return content.strip() if content else "I got an empty response from the model."
    except Exception as e:
        logger.exception("Model error: %s", e)
        return (
            "I couldn't reach the AI provider right now. "
            "Check Railway env vars: TELEGRAM_BOT_TOKEN, NARAROUTER_API_KEY, "
            "NARAROUTER_BASE_URL, and NARAROUTER_MODEL."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        await update.message.reply_text("Please send some text.")
        return

    await update.message.chat.send_action("typing")
    answer = await ask_model(user_text)
    await update.message.reply_text(answer)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram error", exc_info=context.error)


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot with model=%s base_url=%s", NARAROUTER_MODEL, NARAROUTER_BASE_URL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()