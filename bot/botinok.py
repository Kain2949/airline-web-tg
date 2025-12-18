import os
import re
import json
import time
import uuid
import math
import random
import sqlite3
import threading
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

DEFAULT_DB_PATH = PROJECT_ROOT / "airline_lab.db"   # один общий файл БД
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

API_HOST = os.getenv("API_HOST", "0.0.0.0").strip()
API_PORT = int(os.getenv("API_PORT", "1488"))

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "2.0"))
PENDING_TTL_MINUTES = int(os.getenv("PENDING_TTL_MINUTES", "15"))

# GitHub Pages домен (чтобы не душить CORS)
DEFAULT_ALLOWED_ORIGINS = [
    "https://kain2949.github.io",
    "https://*.github.io",
    "https://*.ngrok-free.dev",
]
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").strip()


# =========================
# UTILS
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def normalize_username(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if not s.startswith("@"):
        s = "@" + s
    return s

def gen_code() -> str:
    return f"{random.randint(0, 999999):06d}"

def mask_passport(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return ""
    if len(p) <= 6:
        if len(p) <= 2:
            return "*" * len(p)
        return p[0] + "*" * (len(p) - 2) + p[-1]
    return p[:3] + "*" * (len(p) - 6) + p[-3:]

def parse_birth_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    # DD.MM.YYYY
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", s):
        dd, mm, yyyy = s.split(".")
        return f"{yyyy}-{mm}-{dd}"
    return s  # оставим как есть, но лучше не надо


# =========================
# DB
# =========================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return row is not None

def passengers_schema_ok(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "passengers"):
        return True
    cols = conn.execute("PRAGMA table_info(passengers);").fetchall()
    for c in cols:
        if c["name"] == "passenger_id":
            t = (c["type"] or "").upper()
            return "TEXT" in t
    return False

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # Если пассажиры были INT — сносим passengers+tickets и делаем нормально (как ты хотел)
        if not passengers_schema_ok(conn):
            conn.execute("DROP TABLE IF EXISTS tickets;")
            conn.execute("DROP TABLE IF EXISTS passengers;")
            conn.commit()

        # ===== Telegram tables =====
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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_code_requests (
            request_id  TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- 'register' | 'booking'
            code        TEXT,
            status      TEXT NOT NULL,   -- 'pending' | 'sent' | 'used' | 'cancelled'
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

        # ===== Airline schema =====
        conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_statuses (
            status_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            status_name TEXT NOT NULL UNIQUE
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS planes (
            plane_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            model            TEXT NOT NULL,
            manufacture_year INTEGER NOT NULL,
            seat_capacity    INTEGER NOT NULL
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            flight_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            plane_id       INTEGER NOT NULL,
            flight_number  TEXT NOT NULL,
            departure_city TEXT NOT NULL,
            arrival_city   TEXT NOT NULL,
            flight_date    TEXT NOT NULL, -- YYYY-MM-DD
            flight_time    TEXT NOT NULL, -- HH:MM
            FOREIGN KEY (plane_id) REFERENCES planes(plane_id)
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_flights_date
        ON flights(flight_date);
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id TEXT PRIMARY KEY,  -- @telegram_username
            last_name    TEXT NOT NULL,
            first_name   TEXT NOT NULL,
            middle_name  TEXT,
            passport_no  TEXT NOT NULL,
            birth_date   TEXT NOT NULL,
            phone        TEXT NOT NULL,
            email        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id     INTEGER NOT NULL,
            passenger_id  TEXT NOT NULL,       -- @telegram_username
            status_id     INTEGER NOT NULL,
            seat_no       TEXT NOT NULL,
            price_usd     REAL NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE(flight_id, seat_no),
            FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
            FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
            FOREIGN KEY (status_id) REFERENCES ticket_statuses(status_id)
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tickets_passenger
        ON tickets(passenger_id);
        """)

        conn.commit()

        seed_defaults(conn)

        conn.commit()
    finally:
        conn.close()

def seed_defaults(conn: sqlite3.Connection) -> None:
    # statuses
    for name in ["PENDING", "CONFIRMED", "CANCELLED"]:
        conn.execute("INSERT OR IGNORE INTO ticket_statuses(status_name) VALUES (?);", (name,))

    # planes
    planes_count = conn.execute("SELECT COUNT(*) AS c FROM planes;").fetchone()["c"]
    if planes_count == 0:
        conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Airbus A320', 2014, 180);")
        conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Boeing 737-800', 2012, 189);")
        conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Embraer E195', 2016, 120);")

    flights_count = conn.execute("SELECT COUNT(*) AS c FROM flights;").fetchone()["c"]
    if flights_count == 0:
        plane_ids = [r["plane_id"] for r in conn.execute("SELECT plane_id FROM planes;").fetchall()]
        routes = [
            ("Minsk", "Warsaw"),
            ("Minsk", "Vilnius"),
            ("Minsk", "Istanbul"),
            ("Warsaw", "Berlin"),
            ("Vilnius", "Riga"),
        ]
        base = datetime.now().date()
        for i in range(12):
            d = (base + timedelta(days=i % 7)).isoformat()
            hh = 7 + (i * 2) % 12
            mm = "00" if i % 2 == 0 else "30"
            dep, arr = routes[i % len(routes)]
            plane_id = plane_ids[i % len(plane_ids)]
            fn = f"JP{100 + i}"
            conn.execute("""
                INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (plane_id, fn, dep, arr, d, f"{hh:02d}:{mm}"))

def status_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (name,)).fetchone()
    if not row:
        raise RuntimeError(f"Missing status {name}")
    return int(row["status_id"])

def cleanup_expired_pending(conn: sqlite3.Connection) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_TTL_MINUTES)
    cutoff_iso = cutoff.replace(microsecond=0).isoformat()

    # ticket pending -> cancel (чтобы места не висели вечно)
    sid_pending = status_id(conn, "PENDING")
    sid_cancelled = status_id(conn, "CANCELLED")

    conn.execute("""
        UPDATE tickets
        SET status_id=?, updated_at=?
        WHERE status_id=? AND created_at < ?;
    """, (sid_cancelled, now_utc_iso(), sid_pending, cutoff_iso))

    # code requests pending/sent старые -> cancelled
    conn.execute("""
        UPDATE tg_code_requests
        SET status='cancelled'
        WHERE status IN ('pending','sent') AND created_at < ?;
    """, (cutoff_iso,))


# =========================
# TELEGRAM BOT
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = normalize_username(u.username or "")
    if not username:
        await update.message.reply_text(
            "Слушай… у тебя нет @username.\n"
            "Telegram → Settings → Username. Поставь его и снова жми /start.",
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
        "Ок. Привязала ✅\n"
        "Теперь возвращайся в веб-приложение и запрашивай коды.",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать Telegram к сервису\n"
        "/help — помощь\n\n"
        "Коды подтверждения приходят сюда автоматически."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text(
            "Код красивый, спору нет.\n"
            "Но вводится он в веб-приложении, а не мне в личку 😈"
        )
        return
    await update.message.reply_text("Я бот подтверждений. /start — если ещё не привязан.")


async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        rows = conn.execute("""
            SELECT request_id, username, kind, payload
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 30;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            kind = (r["kind"] or "").strip()
            payload = (r["payload"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?",
                (username,)
            ).fetchone()

            if not user:
                # не нажал /start
                continue

            code = gen_code()

            extra = ""
            if payload:
                try:
                    obj = json.loads(payload)
                except Exception:
                    obj = {}
                if kind == "booking":
                    extra = ""
                    t_id = obj.get("ticket_id")
                    if t_id:
                        row = conn.execute("""
                            SELECT f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time, t.seat_no
                            FROM tickets t
                            JOIN flights f ON f.flight_id=t.flight_id
                            WHERE t.ticket_id=?;
                        """, (int(t_id),)).fetchone()
                        if row:
                            extra = (
                                f"\n\nРейс: <b>{row['flight_number']}</b>\n"
                                f"{row['departure_city']} → {row['arrival_city']}\n"
                                f"Дата/время: <b>{row['flight_date']} {row['flight_time']}</b>\n"
                                f"Место: <b>{row['seat_no']}</b>"
                            )

            title = "Регистрация" if kind == "register" else "Бронирование" if kind == "booking" else kind
            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{title}</b>\n"
                f"Введи этот код в веб-приложении."
                f"{extra}"
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
            LIMIT 30;
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
                    message = (
                        "✅ Регистрация успешна.\n\n"
                        f"ФИО: <b>{obj.get('fio','')}</b>\n"
                        f"Дата рождения: <b>{obj.get('birth_date','')}</b>\n"
                        f"Паспорт: <b>{mask_passport(obj.get('passport_no',''))}</b>"
                    )
                elif kind == "booking_success":
                    message = (
                        "✅ Бронирование подтверждено.\n\n"
                        f"Рейс: <b>{obj.get('flight_number','')}</b>\n"
                        f"{obj.get('route','')}\n"
                        f"Дата/время: <b>{obj.get('dt','')}</b>\n"
                        f"Место: <b>{obj.get('seat_no','')}</b>\n"
                        f"Цена: <b>${obj.get('price_usd','')}</b>"
                    )
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
        await asyncio_sleep(POLL_SECONDS)

async def asyncio_sleep(seconds: float) -> None:
    # чтобы не тянуть asyncio наверх — маленький костыль
    import asyncio
    await asyncio.sleep(seconds)

async def post_init(app: Application) -> None:
    app.create_task(background_loop(app))


# =========================
# API (FastAPI)
# =========================

api = FastAPI(title="Airline API", version="1.0")

if ALLOWED_ORIGINS:
    origins = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]
else:
    origins = DEFAULT_ALLOWED_ORIGINS + ["*"]

api.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AuthStartIn(BaseModel):
    telegram_username: str
    purpose: str  # 'register'

class RegisterCompleteIn(BaseModel):
    telegram_username: str
    code: str
    last_name: str
    first_name: str
    middle_name: str | None = ""
    passport_no: str
    birth_date: str
    phone: str
    email: str

class BookingStartIn(BaseModel):
    telegram_username: str
    flight_id: int
    seat_no: str
    price_usd: float

class BookingConfirmIn(BaseModel):
    telegram_username: str
    code: str


def seats_for_capacity(capacity: int) -> list[str]:
    letters = ["A", "B", "C", "D", "E", "F"]
    res = []
    for i in range(capacity):
        row = i // 6 + 1
        letter = letters[i % 6]
        res.append(f"{row}{letter}")
    return res

def api_error(msg: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=msg)


@api.get("/health")
def health():
    return {"ok": True, "db": str(DB_PATH), "ts": now_utc_iso()}


@api.post("/api/auth/start")
def auth_start(body: AuthStartIn):
    username = normalize_username(body.telegram_username)
    purpose = (body.purpose or "").strip().lower()

    if purpose not in ["register"]:
        api_error("Invalid purpose", 422)
    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        api_error("Некорректный @username", 422)

    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        req_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, kind, status, payload, created_at)
            VALUES (?, ?, 'register', 'pending', ?, ?);
        """, (req_id, username, "", now_utc_iso()))
        conn.commit()
        return {"ok": True, "request_id": req_id}
    finally:
        conn.close()


@api.post("/api/register/complete")
def register_complete(body: RegisterCompleteIn):
    username = normalize_username(body.telegram_username)
    code = (body.code or "").strip()

    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        api_error("Некорректный @username", 422)
    if not re.fullmatch(r"\d{6}", code):
        api_error("Код должен быть из 6 цифр", 422)

    birth = parse_birth_date(body.birth_date)

    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        req = conn.execute("""
            SELECT request_id
            FROM tg_code_requests
            WHERE username=? AND kind='register' AND status='sent' AND code=?
            ORDER BY sent_at DESC
            LIMIT 1;
        """, (username, code)).fetchone()

        if not req:
            api_error("Неверный код или код не отправлялся. Запроси новый.", 403)

        ts = now_utc_iso()

        conn.execute("""
            INSERT INTO passengers(passenger_id, last_name, first_name, middle_name, passport_no, birth_date, phone, email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(passenger_id) DO UPDATE SET
                last_name=excluded.last_name,
                first_name=excluded.first_name,
                middle_name=excluded.middle_name,
                passport_no=excluded.passport_no,
                birth_date=excluded.birth_date,
                phone=excluded.phone,
                email=excluded.email,
                updated_at=excluded.updated_at;
        """, (
            username,
            (body.last_name or "").strip(),
            (body.first_name or "").strip(),
            (body.middle_name or "").strip(),
            (body.passport_no or "").strip(),
            birth,
            (body.phone or "").strip(),
            (body.email or "").strip(),
            ts, ts
        ))

        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (ts, req["request_id"]))

        fio = f"{body.last_name.strip()} {body.first_name.strip()} {body.middle_name.strip()}".strip()

        conn.execute("""
            INSERT INTO tg_notifications(username, kind, message, payload, status, created_at)
            VALUES (?, 'registration_success', '', ?, 'pending', ?);
        """, (
            username,
            json.dumps({
                "fio": fio,
                "birth_date": birth,
                "passport_no": body.passport_no.strip()
            }, ensure_ascii=False),
            ts
        ))

        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api.get("/api/flights")
def flights_list():
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            ORDER BY f.flight_date, f.flight_time;
        """).fetchall()

        res = []
        for r in rows:
            # “цена в билетах” — ок, но предложим дефолт
            price_suggested = float(99 + (r["flight_id"] % 7) * 25)

            res.append({
                "flight_id": int(r["flight_id"]),
                "flight_number": r["flight_number"],
                "departure_city": r["departure_city"],
                "arrival_city": r["arrival_city"],
                "flight_date": r["flight_date"],
                "flight_time": r["flight_time"],
                "plane_model": r["plane_model"],
                "seat_capacity": int(r["seat_capacity"]),
                "price_suggested": price_suggested
            })

        return {"ok": True, "flights": res}
    finally:
        conn.close()


@api.get("/api/flights/{flight_id}/seats")
def flight_seats(flight_id: int):
    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        row = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()
        if not row:
            api_error("Рейс не найден", 404)

        cap = int(row["seat_capacity"])
        all_seats = seats_for_capacity(cap)

        sid_confirmed = status_id(conn, "CONFIRMED")
        sid_pending = status_id(conn, "PENDING")

        taken_rows = conn.execute("""
            SELECT seat_no
            FROM tickets
            WHERE flight_id=? AND status_id IN (?, ?);
        """, (flight_id, sid_confirmed, sid_pending)).fetchall()

        taken = sorted({t["seat_no"] for t in taken_rows})
        available = [s for s in all_seats if s not in set(taken)]

        return {"ok": True, "capacity": cap, "taken": taken, "available": available}
    finally:
        conn.close()


@api.post("/api/booking/start")
def booking_start(body: BookingStartIn):
    username = normalize_username(body.telegram_username)
    seat_no = (body.seat_no or "").strip().upper()
    price = float(body.price_usd)

    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        api_error("Некорректный @username", 422)
    if price <= 0:
        api_error("Цена должна быть > 0", 422)

    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        p = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
        if not p:
            api_error("Сначала зарегистрируйся.", 403)

        f = conn.execute("""
            SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (int(body.flight_id),)).fetchone()
        if not f:
            api_error("Рейс не найден", 404)

        cap = int(f["seat_capacity"])
        valid_seats = set(seats_for_capacity(cap))
        if seat_no not in valid_seats:
            api_error("Некорректное место", 422)

        sid_confirmed = status_id(conn, "CONFIRMED")
        sid_pending = status_id(conn, "PENDING")

        exists = conn.execute("""
            SELECT ticket_id
            FROM tickets
            WHERE flight_id=? AND seat_no=? AND status_id IN (?, ?)
            LIMIT 1;
        """, (int(body.flight_id), seat_no, sid_confirmed, sid_pending)).fetchone()

        if exists:
            api_error("Место уже занято. Выбери другое.", 409)

        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (int(body.flight_id), username, sid_pending, seat_no, price, ts, ts))
        ticket_id = conn.execute("SELECT last_insert_rowid() AS id;").fetchone()["id"]

        req_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, kind, status, payload, created_at)
            VALUES (?, ?, 'booking', 'pending', ?, ?);
        """, (
            req_id,
            username,
            json.dumps({"ticket_id": int(ticket_id)}, ensure_ascii=False),
            ts
        ))

        conn.commit()

        return {"ok": True, "request_id": req_id}
    finally:
        conn.close()


@api.post("/api/booking/confirm")
def booking_confirm(body: BookingConfirmIn):
    username = normalize_username(body.telegram_username)
    code = (body.code or "").strip()

    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        api_error("Некорректный @username", 422)
    if not re.fullmatch(r"\d{6}", code):
        api_error("Код должен быть из 6 цифр", 422)

    conn = db_connect()
    try:
        cleanup_expired_pending(conn)

        req = conn.execute("""
            SELECT request_id, payload
            FROM tg_code_requests
            WHERE username=? AND kind='booking' AND status='sent' AND code=?
            ORDER BY sent_at DESC
            LIMIT 1;
        """, (username, code)).fetchone()

        if not req:
            api_error("Неверный код или он устарел. Запроси новый.", 403)

        try:
            obj = json.loads(req["payload"] or "{}")
        except Exception:
            obj = {}

        ticket_id = int(obj.get("ticket_id") or 0)
        if ticket_id <= 0:
            api_error("Внутренняя ошибка бронирования.", 500)

        sid_pending = status_id(conn, "PENDING")
        sid_confirmed = status_id(conn, "CONFIRMED")

        t = conn.execute("""
            SELECT t.ticket_id, t.flight_id, t.seat_no, t.price_usd, t.status_id,
                   f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            WHERE t.ticket_id=? AND t.passenger_id=?;
        """, (ticket_id, username)).fetchone()

        if not t:
            api_error("Билет не найден.", 404)
        if int(t["status_id"]) != sid_pending:
            api_error("Этот билет уже не в статусе ожидания.", 409)

        ts = now_utc_iso()

        conn.execute("""
            UPDATE tickets
            SET status_id=?, updated_at=?
            WHERE ticket_id=?;
        """, (sid_confirmed, ts, ticket_id))

        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (ts, req["request_id"]))

        conn.execute("""
            INSERT INTO tg_notifications(username, kind, message, payload, status, created_at)
            VALUES (?, 'booking_success', '', ?, 'pending', ?);
        """, (
            username,
            json.dumps({
                "flight_number": t["flight_number"],
                "route": f"{t['departure_city']} → {t['arrival_city']}",
                "dt": f"{t['flight_date']} {t['flight_time']}",
                "seat_no": t["seat_no"],
                "price_usd": f"{float(t['price_usd']):.2f}",
            }, ensure_ascii=False),
            ts
        ))

        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api.get("/api/my/flights")
def my_flights(telegram_username: str):
    username = normalize_username(telegram_username)
    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        api_error("Некорректный @username", 422)

    conn = db_connect()
    try:
        sid_confirmed = status_id(conn, "CONFIRMED")
        rows = conn.execute("""
            SELECT t.ticket_id, t.seat_no, t.price_usd, t.created_at,
                   f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE t.passenger_id=? AND t.status_id=?
            ORDER BY f.flight_date, f.flight_time;
        """, (username, sid_confirmed)).fetchall()

        items = []
        for r in rows:
            items.append({
                "ticket_id": int(r["ticket_id"]),
                "flight_number": r["flight_number"],
                "route": f"{r['departure_city']} → {r['arrival_city']}",
                "dt": f"{r['flight_date']} {r['flight_time']}",
                "seat_no": r["seat_no"],
                "plane_model": r["plane_model"],
                "price_usd": float(r["price_usd"]),
                "created_at": r["created_at"],
            })

        return {"ok": True, "items": items}
    finally:
        conn.close()


def run_api() -> None:
    uvicorn.run(api, host=API_HOST, port=API_PORT, log_level="info", reload=False)


# =========================
# MAIN
# =========================

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой.\n"
            "Сначала задай переменную окружения BOT_TOKEN и запускай снова."
        )

    db_init()
    print(f"[bot] DB: {DB_PATH}")
    print(f"[api] http://{API_HOST}:{API_PORT}")

    # API в отдельном потоке (один файл, без server.py)
    t = threading.Thread(target=run_api, daemon=True)
    t.start()

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
