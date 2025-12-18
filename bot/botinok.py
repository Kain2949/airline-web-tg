import os
import re
import json
import time
import uuid
import random
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn


# =========================
# CONFIG
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "airline_app.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "2.0"))
CODE_TTL_SECONDS = int(os.getenv("CODE_TTL_SECONDS", "600"))  # 10 мин по умолчанию

REQUIRE_USERNAME = True


# =========================
# DB HELPERS
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # Telegram users
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

        # Code requests (web -> db, bot -> sends and fills code)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_code_requests (
            request_id  TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- 'register' | 'booking'
            code        TEXT,
            status      TEXT NOT NULL,   -- 'pending' | 'sent' | 'used' | 'cancelled' | 'expired'
            payload     TEXT,
            created_at  TEXT NOT NULL,
            sent_at     TEXT,
            used_at     TEXT
        );
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_code_pending
        ON tg_code_requests(status, created_at);
        """)

        # Notifications (web/db -> bot -> messages)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_notifications (
            notif_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- 'registration_success' | 'booking_success'
            message     TEXT,
            payload     TEXT,
            status      TEXT NOT NULL,   -- 'pending' | 'sent'
            created_at  TEXT NOT NULL,
            sent_at     TEXT
        );
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_notif_pending
        ON tg_notifications(status, created_at);
        """)

        # Passenger data (минимум для регистрации)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_name    TEXT NOT NULL,
            first_name   TEXT NOT NULL,
            middle_name  TEXT,
            passport_no  TEXT NOT NULL UNIQUE,
            birth_date   TEXT NOT NULL,      -- YYYY-MM-DD
            phone        TEXT NOT NULL,
            email        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        """)

        # Link telegram username -> passenger
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_passengers (
            username     TEXT PRIMARY KEY,
            passenger_id INTEGER NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            FOREIGN KEY(passenger_id) REFERENCES passengers(passenger_id) ON DELETE CASCADE
        );
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
    p = (passport or "").strip()
    if len(p) <= 6:
        if len(p) <= 2:
            return "*" * len(p)
        return p[0] + "*" * (len(p) - 2) + p[-1]
    return p[:3] + "*" * (len(p) - 6) + p[-3:]

def make_request_id() -> str:
    return uuid.uuid4().hex

def kind_from_purpose(purpose: str) -> str:
    p = (purpose or "").strip().lower()
    if p in ("register", "registration", "reg"):
        return "register"
    if p in ("booking", "book"):
        return "booking"
    return p or "register"

def ensure_user_linked(conn: sqlite3.Connection, username: str) -> bool:
    row = conn.execute("SELECT chat_id FROM tg_users WHERE username=?", (username,)).fetchone()
    return bool(row)

def code_is_expired(created_at_iso: str) -> bool:
    try:
        created = parse_iso(created_at_iso)
    except Exception:
        return True
    return datetime.now(timezone.utc) - created > timedelta(seconds=CODE_TTL_SECONDS)


# =========================
# TELEGRAM BOT HANDLERS
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = normalize_username(u.username or "")
    if REQUIRE_USERNAME and not username:
        await update.message.reply_text(
            "У тебя не задан @username в Telegram.\n"
            "Настройки → Username → поставь его → потом снова /start.",
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
        "Готово. Я тебя привязала ✅\n"
        "Возвращайся в веб-приложение и запрашивай коды.",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать Telegram к сервису\n"
        "/help — помощь\n\n"
        "Коды приходят сюда автоматически, когда ты запрашиваешь их в веб-приложении."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text(
            "Код вижу. Но вводить его надо в веб-приложении, а не мне в личку 😈"
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
            SELECT request_id, username, kind, payload, created_at
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 50;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            kind = (r["kind"] or "").strip()
            created_at = (r["created_at"] or "").strip()

            if code_is_expired(created_at):
                conn.execute("""
                    UPDATE tg_code_requests
                    SET status='expired'
                    WHERE request_id=?;
                """, (req_id,))
                conn.commit()
                continue

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?",
                (username,)
            ).fetchone()

            if not user:
                # пользователь не сделал /start — ждём
                continue

            code = gen_code()
            kind_human = "Регистрация" if kind == "register" else ("Бронирование" if kind == "booking" else kind)

            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{kind_human}</b>\n\n"
                f"Введи этот код в веб-приложении."
            )

            try:
                await app.bot.send_message(
                    chat_id=int(user["chat_id"]),
                    text=msg,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
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
            LIMIT 50;
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
                try:
                    obj = json.loads(payload)
                except Exception:
                    obj = {}

                if kind == "registration_success":
                    fio = obj.get("fio") or ""
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
    while True:
        try:
            await process_pending_codes(app)
            await process_pending_notifications(app)
        except Exception:
            pass
        await asyncio.sleep(POLL_SECONDS)


# =========================
# FASTAPI (IN BOT PROCESS)
# =========================

api = FastAPI(title="Airline Bot API")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # для GitHub Pages проще так
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuthStartIn(BaseModel):
    telegram_username: str
    purpose: str  # 'register' | 'booking' | ...

class AuthVerifyIn(BaseModel):
    telegram_username: str
    purpose: str
    code: str

class PassengerRegisterIn(BaseModel):
    telegram_username: str
    code: str

    last_name: str
    first_name: str
    middle_name: str | None = None

    passport_no: str
    birth_date: str     # YYYY-MM-DD
    phone: str
    email: str

@api.get("/health")
def health():
    return {"ok": True, "ts": now_utc_iso()}

@api.post("/api/auth/start")
def auth_start(data: AuthStartIn):
    username = normalize_username(data.telegram_username)
    kind = kind_from_purpose(data.purpose)

    if not username:
        raise HTTPException(status_code=422, detail="telegram_username required")

    conn = db_connect()
    try:
        if not ensure_user_linked(conn, username):
            raise HTTPException(
                status_code=409,
                detail="user_not_linked_start_bot_first"
            )

        req_id = make_request_id()
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, kind, code, status, payload, created_at)
            VALUES (?, ?, ?, NULL, 'pending', NULL, ?);
        """, (req_id, username, kind, now_utc_iso()))
        conn.commit()

        return {"ok": True, "request_id": req_id, "status": "pending"}
    finally:
        conn.close()

@api.post("/api/auth/verify")
def auth_verify(data: AuthVerifyIn):
    username = normalize_username(data.telegram_username)
    kind = kind_from_purpose(data.purpose)
    code = (data.code or "").strip()

    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=422, detail="code must be 6 digits")

    conn = db_connect()
    try:
        row = conn.execute("""
            SELECT request_id, created_at
            FROM tg_code_requests
            WHERE username=? AND kind=? AND status='sent' AND code=?
            ORDER BY created_at DESC
            LIMIT 1;
        """, (username, kind, code)).fetchone()

        if not row:
            raise HTTPException(status_code=400, detail="invalid_code")

        if code_is_expired(row["created_at"]):
            conn.execute("""
                UPDATE tg_code_requests
                SET status='expired'
                WHERE request_id=?;
            """, (row["request_id"],))
            conn.commit()
            raise HTTPException(status_code=400, detail="code_expired")

        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (now_utc_iso(), row["request_id"]))
        conn.commit()

        return {"ok": True}
    finally:
        conn.close()

@api.post("/api/passengers/register")
def passengers_register(data: PassengerRegisterIn):
    username = normalize_username(data.telegram_username)
    code = (data.code or "").strip()

    if not username:
        raise HTTPException(status_code=422, detail="telegram_username required")
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=422, detail="code must be 6 digits")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (data.birth_date or "").strip()):
        raise HTTPException(status_code=422, detail="birth_date must be YYYY-MM-DD")
    if not (data.passport_no or "").strip():
        raise HTTPException(status_code=422, detail="passport_no required")

    conn = db_connect()
    try:
        # verify code (register)
        row = conn.execute("""
            SELECT request_id, created_at
            FROM tg_code_requests
            WHERE username=? AND kind='register' AND status='sent' AND code=?
            ORDER BY created_at DESC
            LIMIT 1;
        """, (username, code)).fetchone()

        if not row:
            raise HTTPException(status_code=400, detail="invalid_code")

        if code_is_expired(row["created_at"]):
            conn.execute("""
                UPDATE tg_code_requests
                SET status='expired'
                WHERE request_id=?;
            """, (row["request_id"],))
            conn.commit()
            raise HTTPException(status_code=400, detail="code_expired")

        # mark code used
        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (now_utc_iso(), row["request_id"]))

        ts = now_utc_iso()
        passport_no = data.passport_no.strip()

        # upsert passenger by passport_no
        existing = conn.execute("""
            SELECT passenger_id FROM passengers WHERE passport_no=?;
        """, (passport_no,)).fetchone()

        if existing:
            pid = int(existing["passenger_id"])
            conn.execute("""
                UPDATE passengers
                SET last_name=?, first_name=?, middle_name=?, birth_date=?, phone=?, email=?, updated_at=?
                WHERE passenger_id=?;
            """, (
                data.last_name.strip(),
                data.first_name.strip(),
                (data.middle_name or "").strip() or None,
                data.birth_date.strip(),
                data.phone.strip(),
                data.email.strip(),
                ts,
                pid
            ))
        else:
            cur = conn.execute("""
                INSERT INTO passengers(last_name, first_name, middle_name, passport_no, birth_date, phone, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                data.last_name.strip(),
                data.first_name.strip(),
                (data.middle_name or "").strip() or None,
                passport_no,
                data.birth_date.strip(),
                data.phone.strip(),
                data.email.strip(),
                ts,
                ts
            ))
            pid = int(cur.lastrowid)

        # link telegram -> passenger
        conn.execute("""
            INSERT INTO tg_passengers(username, passenger_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                passenger_id=excluded.passenger_id,
                updated_at=excluded.updated_at;
        """, (username, pid, ts, ts))

        # notify user in Telegram
        fio = f"{data.last_name.strip()} {data.first_name.strip()}".strip()
        if (data.middle_name or "").strip():
            fio += f" {(data.middle_name or '').strip()}"

        payload = json.dumps({
            "fio": fio,
            "birth_date": data.birth_date.strip(),
            "passport_no": passport_no
        }, ensure_ascii=False)

        conn.execute("""
            INSERT INTO tg_notifications(username, kind, message, payload, status, created_at)
            VALUES (?, 'registration_success', NULL, ?, 'pending', ?);
        """, (username, payload, ts))

        conn.commit()

        return {"ok": True, "passenger_id": pid}
    finally:
        conn.close()


async def run_api_server() -> None:
    config = uvicorn.Config(
        api,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)
    await server.serve()


async def post_init(app: Application) -> None:
    # DB init + background loops + API server
    db_init()
    app.create_task(background_loop(app))
    app.create_task(run_api_server())


# =========================
# MAIN
# =========================

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой.\n"
            "Сделай так: set BOT_TOKEN=... (в cmd) и запускай снова.\n"
            "И да — токен, который ты прислал в чат, уже скомпрометирован. Перевыпусти."
        )

    print(f"[bot] DB: {DB_PATH}")
    print(f"[api] http://{API_HOST}:{API_PORT}")
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
