import os
import re
import time
import json
import sqlite3
import random
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# =========================
# CONFIG
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "airline_app.db"

BOT_TOKEN = os.getenv("8596097444:AAHmyMfDVeSkhBGkXxbqF23H5622hquS-vM", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "2.0"))

# Если хочешь жёстко требовать @username у пользователя
REQUIRE_USERNAME = True

# =========================
# DB HELPERS
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # Пользователи Telegram
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_users (
            username    TEXT PRIMARY KEY,
            chat_id     INTEGER NOT NULL,
            first_name  TEXT,
            last_name   TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        """)

        # Запросы на отправку кода (веб -> бот)
        # backend/web вставляют сюда запись со status='pending' и username='@name'
        # бот проставляет code и status='sent'
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_code_requests (
            request_id  TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- 'register' | 'booking'
            code        TEXT,            -- бот поставит
            status      TEXT NOT NULL,   -- 'pending' | 'sent' | 'used' | 'cancelled'
            payload     TEXT,            -- JSON строка (опционально)
            created_at  TEXT NOT NULL,
            sent_at     TEXT,
            used_at     TEXT
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_code_pending
        ON tg_code_requests(status, created_at);
        """)

        # Уведомления (backend/web -> бот), чтобы бот написал пользователю итог
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_notifications (
            notif_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- 'registration_success' | 'booking_success' | ...
            message     TEXT,            -- если есть готовый текст
            payload     TEXT,            -- или JSON, из которого бот соберёт текст
            status      TEXT NOT NULL,   -- 'pending' | 'sent'
            created_at  TEXT NOT NULL,
            sent_at     TEXT
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_notif_pending
        ON tg_notifications(status, created_at);
        """)

        conn.commit()
    finally:
        conn.close()

def normalize_username(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if not s.startswith("@"):
        s = "@" + s
    return s

def gen_code() -> str:
    return f"{random.randint(0, 999999):06d}"

def mask_passport(passport: str) -> str:
    # показываем первые 3 и последние 3 символа, остальное ****
    p = (passport or "").strip()
    if len(p) <= 6:
        if len(p) <= 2:
            return "*" * len(p)
        return p[0] + "*" * (len(p) - 2) + p[-1]
    return p[:3] + "*" * (len(p) - 6) + p[-3:]

# =========================
# BOT HANDLERS
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = normalize_username(u.username or "")
    if REQUIRE_USERNAME and not username:
        await update.message.reply_text(
            "У тебя не задан @username в Telegram.\n"
            "Зайди в настройки Telegram → Username, поставь его, потом снова жми /start.",
        )
        return

    conn = db_connect()
    try:
        ts = now_utc_iso()
        conn.execute("""
        INSERT INTO tg_users(username, chat_id, first_name, last_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            chat_id=excluded.chat_id,
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            updated_at=excluded.updated_at;
        """, (username, chat.id, u.first_name, u.last_name, ts, ts))
        conn.commit()
    finally:
        conn.close()

    await update.message.reply_text(
        "Готово. Я тебя привязала.\n"
        "Теперь можешь возвращаться в веб-приложение и получать коды подтверждения.",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать твой аккаунт Telegram к сервису\n"
        "/help — помощь\n\n"
        "Коды подтверждения приходят сюда автоматически, когда ты запрашиваешь их в веб-приложении."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Просто чтоб не молчал, если пользователь пишет что-то в чат
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text(
            "Код получил(а). Но вводить его нужно в веб-приложении. Тут я его не принимаю 😈"
        )
        return
    await update.message.reply_text("Я бот подтверждений. Жми /start, если ещё не привязан.")

# =========================
# BACKGROUND WORKER
# =========================

async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT request_id, username, kind, payload
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 20;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            kind = (r["kind"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?",
                (username,)
            ).fetchone()

            if not user:
                # Пользователь не нажал /start — оставляем pending
                continue

            code = gen_code()
            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{'Регистрация' if kind=='register' else 'Бронирование' if kind=='booking' else kind}</b>\n\n"
                f"Введи этот код в веб-приложении."
            )

            try:
                await app.bot.send_message(
                    chat_id=int(user["chat_id"]),
                    text=msg,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                # если не смогли отправить (например, бот заблокирован) — не жжём БД
                continue

            conn.execute("""
                UPDATE tg_code_requests
                SET code=?, status='sent', sent_at=?
                WHERE request_id=?;
            """, (code, now_utc_iso(), req_id))
            conn.commit()
    finally:
        conn.close()

async def process_pending_notifications(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT notif_id, username, kind, message, payload
            FROM tg_notifications
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 20;
        """).fetchall()

        for r in rows:
            notif_id = int(r["notif_id"])
            username = normalize_username(r["username"])
            kind = (r["kind"] or "").strip()
            message = (r["message"] or "").strip()
            payload = (r["payload"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?",
                (username,)
            ).fetchone()
            if not user:
                continue

            if not message and payload:
                # пробуем собрать из JSON
                try:
                    obj = json.loads(payload)
                except Exception:
                    obj = {}

                if kind == "registration_success":
                    fio = obj.get("fio") or obj.get("full_name") or ""
                    bday = obj.get("birth_date") or ""
                    passport = obj.get("passport_no") or ""
                    message = (
                        "✅ Регистрация успешна.\n\n"
                        f"ФИО: <b>{fio}</b>\n"
                        f"Дата рождения: <b>{bday}</b>\n"
                        f"Паспорт: <b>{mask_passport(passport)}</b>"
                    )
                elif kind == "booking_success":
                    details = obj.get("details") or ""
                    message = "✅ Бронирование подтверждено.\n\n" + str(details)
                else:
                    message = "✅ Готово."

            if not message:
                message = "✅ Готово."

            try:
                await app.bot.send_message(
                    chat_id=int(user["chat_id"]),
                    text=message,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                continue

            conn.execute("""
                UPDATE tg_notifications
                SET status='sent', sent_at=?
                WHERE notif_id=?;
            """, (now_utc_iso(), notif_id))
            conn.commit()
    finally:
        conn.close()

async def background_loop(app: Application) -> None:
    # вечный цикл, пока бот жив
    while True:
        try:
            await process_pending_codes(app)
            await process_pending_notifications(app)
        except Exception:
            # чтобы бот не падал от одного кривого запроса в БД
            pass
        await asyncio.sleep(POLL_SECONDS)

async def post_init(app: Application) -> None:
    # стартуем фонового воркера
    app.create_task(background_loop(app))

# =========================
# MAIN
# =========================

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой. Либо впиши токен в переменную окружения BOT_TOKEN,\n"
            "либо прямо в код (не советую для публичного репо)."
        )

    db_init()
    print(f"[bot] DB: {DB_PATH}")
    print("[bot] starting...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
