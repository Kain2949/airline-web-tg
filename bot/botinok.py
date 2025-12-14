import asyncio
import json
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BRIDGE_DB = "airline_bridge.db"
BOT_TOKEN = "8596097444:AAHmyMfDVeSkhBGkXxbqF23H5622hquS-vM"


def get_bridge_conn():
    conn = sqlite3.connect(BRIDGE_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = f"@{user.username}" if user.username else str(user.id)
    chat_id = update.effective_chat.id

    conn = get_bridge_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tg_users(telegram_username, chat_id)
        VALUES (?, ?)
        ON CONFLICT(telegram_username) DO UPDATE SET chat_id = excluded.chat_id
        """,
        (username, chat_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        "Привет. Я буду присылать тебе коды подтверждения и уведомления о регистрации/бронированиях."
    )


async def send_pending_codes(app):
    conn = get_bridge_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT c.id, c.telegram_username, c.code, c.purpose, u.chat_id
        FROM tg_codes c
        JOIN tg_users u ON u.telegram_username = c.telegram_username
        WHERE c.is_sent = 0
        """
    )
    rows = cur.fetchall()

    for row in rows:
        chat_id = row["chat_id"]
        code = row["code"]
        purpose = row["purpose"]
        text = f"Твой код подтверждения ({purpose}): {code}"

        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
            cur.execute("UPDATE tg_codes SET is_sent = 1 WHERE id = ?", (row["id"],))
        except Exception as e:
            print("Ошибка отправки кода:", e)

    conn.commit()
    conn.close()


def mask_passport(passport_no: str) -> str:
    s = passport_no.strip()
    if len(s) <= 6:
        if len(s) <= 2:
            return "*" * len(s)
        return s[0] + "*" * (len(s) - 2) + s[-1]
    return s[:3] + "*" * (len(s) - 6) + s[-3:]


async def send_notifications(app):
    conn = get_bridge_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT n.id, n.telegram_username, n.message_type, n.payload_json, u.chat_id
        FROM tg_notifications n
        JOIN tg_users u ON u.telegram_username = n.telegram_username
        WHERE n.is_sent = 0
        """
    )
    rows = cur.fetchall()

    for row in rows:
        chat_id = row["chat_id"]
        msg_type = row["message_type"]
        payload = json.loads(row["payload_json"])

        if msg_type == "registration_success":
            fio = f'{payload["last_name"]} {payload["first_name"]} {payload.get("middle_name","")}'.strip()
            birth = payload["birth_date"]
            passport = mask_passport(payload["passport_no"])
            text = (
                "Регистрация в системе авиакомпании выполнена успешно.\n\n"
                f"ФИО: {fio}\n"
                f"Дата рождения: {birth}\n"
                f"Паспорт: {passport}"
            )
        else:
            text = "Уведомление: неизвестный тип."

        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
            cur.execute("UPDATE tg_notifications SET is_sent = 1 WHERE id = ?", (row["id"],))
        except Exception as e:
            print("Ошибка отправки уведомления:", e)

    conn.commit()
    conn.close()


async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    job_queue = app.job_queue
    job_queue.run_repeating(lambda ctx: send_pending_codes(app), interval=5, first=5)
    job_queue.run_repeating(lambda ctx: send_notifications(app), interval=5, first=5)

    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
