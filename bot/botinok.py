import os
import re
import json
import time
import uuid
import random
import sqlite3
import threading
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

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
DEFAULT_DB_PATH = PROJECT_ROOT / "airline_lab.db"

DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "1488"))

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "1.0"))

REQUIRE_USERNAME = True  # /start требует @username

# =========================
# HELPERS
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def norm_username(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith("@"):
        u = "@" + u
    return u

def gen_code() -> str:
    return f"{random.randint(0, 999999):06d}"

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return {r["name"] for r in rows}

def ensure_column(conn: sqlite3.Connection, table: str, col_def: str) -> None:
    # col_def like: "purpose TEXT"
    col_name = col_def.split()[0].strip()
    cols = table_cols(conn, table)
    if col_name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def};")

def excel_letters(i0: int) -> str:
    # 0->A, 25->Z, 26->AA...
    n = i0
    s = ""
    while True:
        n, r = divmod(n, 26)
        s = chr(65 + r) + s
        if n == 0:
            break
        n -= 1
    return s

def seats_for_capacity(capacity: int) -> list[str]:
    # 6 мест в ряду, ряды буквами
    if capacity not in (60, 120, 180):
        # на всякий — округлим вниз до кратного 6
        capacity = max(6, (capacity // 6) * 6)
    rows = capacity // 6
    out = []
    for r in range(rows):
        row = excel_letters(r)
        for c in range(1, 7):
            out.append(f"{row}{c}")
    return out

def stable_price(flight_id: int) -> float:
    # стабильная цена, чтобы не прыгала
    base = 120.0 + (flight_id % 37) * 6.5
    wobble = (flight_id % 9) * 1.1
    return round(base + wobble, 2)

# =========================
# DB INIT + SEED
# =========================

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # telegram bindings
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_users (
            username   TEXT PRIMARY KEY,
            chat_id    INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        """)

        # requests for codes
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_code_requests (
            request_id TEXT PRIMARY KEY,
            username   TEXT NOT NULL,
            purpose    TEXT NOT NULL,     -- 'register' | 'login' | 'booking'
            code       TEXT,
            status     TEXT NOT NULL,     -- 'pending' | 'sent' | 'used' | 'cancelled'
            payload    TEXT,              -- JSON
            created_at TEXT NOT NULL,
            sent_at    TEXT,
            used_at    TEXT
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_code_pending
        ON tg_code_requests(status, created_at);
        """)

        # notifications (optional, but nice)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_notifications (
            notif_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            message    TEXT NOT NULL,
            status     TEXT NOT NULL,     -- 'pending' | 'sent'
            created_at TEXT NOT NULL,
            sent_at    TEXT
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_notif_pending
        ON tg_notifications(status, created_at);
        """)

        # sessions
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            username   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user
        ON sessions(username);
        """)

        # core airline tables
        conn.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id TEXT PRIMARY KEY,     -- Telegram @username
            last_name    TEXT NOT NULL,
            first_name   TEXT NOT NULL,
            middle_name  TEXT,
            passport_no  TEXT NOT NULL,
            phone        TEXT NOT NULL,
            email        TEXT NOT NULL
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
            flight_date    TEXT NOT NULL,   -- YYYY-MM-DD
            flight_time    TEXT NOT NULL    -- HH:MM
        );
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_flights_date
        ON flights(flight_date);
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_flights_route_date
        ON flights(departure_city, arrival_city, flight_date);
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_statuses (
            status_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            status_name TEXT NOT NULL UNIQUE
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id     INTEGER NOT NULL,
            passenger_id  TEXT NOT NULL,
            status_id     INTEGER NOT NULL,
            seat_no       TEXT NOT NULL,
            price_usd     REAL NOT NULL
        );
        """)

        conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_seat
        ON tickets(flight_id, seat_no);
        """)

        # --- migrations (на случай старых версий) ---
        ensure_column(conn, "tg_code_requests", "purpose TEXT")
        ensure_column(conn, "tg_code_requests", "payload TEXT")
        ensure_column(conn, "tg_users", "created_at TEXT")
        ensure_column(conn, "tg_users", "updated_at TEXT")

        conn.commit()

        seed_statuses(conn)
        seed_planes(conn)
        seed_flights_if_needed(conn, target=300)

        conn.commit()
    finally:
        conn.close()

def seed_statuses(conn: sqlite3.Connection) -> None:
    have = {r["status_name"] for r in conn.execute("SELECT status_name FROM ticket_statuses;").fetchall()}
    if "BOOKED" not in have:
        conn.execute("INSERT INTO ticket_statuses(status_name) VALUES ('BOOKED');")

def get_status_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (name,)).fetchone()
    if not row:
        raise RuntimeError("ticket_statuses missing")
    return int(row["status_id"])

def seed_planes(conn: sqlite3.Connection) -> None:
    cnt = conn.execute("SELECT COUNT(*) AS c FROM planes;").fetchone()["c"]
    if int(cnt) > 0:
        return
    planes = [
        ("Boeing 737-600", 2014, 60),
        ("Airbus A320", 2017, 120),
        ("Airbus A321", 2019, 180),
    ]
    conn.executemany(
        "INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES (?, ?, ?);",
        planes
    )

def seed_flights_if_needed(conn: sqlite3.Connection, target: int = 300) -> None:
    cnt = int(conn.execute("SELECT COUNT(*) AS c FROM flights;").fetchone()["c"])
    if cnt >= target:
        return

    cities = [
        "Minsk, BY", "Warsaw, PL", "Berlin, DE", "Prague, CZ", "Vienna, AT",
        "Riga, LV", "Vilnius, LT", "Paris, FR", "Rome, IT", "Madrid, ES",
        "London, UK", "Oslo, NO", "Stockholm, SE", "Helsinki, FI", "Zurich, CH",
        "Istanbul, TR", "Athens, GR", "Budapest, HU", "Brussels, BE", "Dublin, IE",
    ]

    plane_ids = [int(r["plane_id"]) for r in conn.execute("SELECT plane_id FROM planes;").fetchall()]
    rnd = random.Random(1337)

    start_date = datetime.now().date() + timedelta(days=1)

    to_add = target - cnt
    rows = []
    used_numbers = {r["flight_number"] for r in conn.execute("SELECT flight_number FROM flights;").fetchall()}

    def gen_number(k: int) -> str:
        # AB123 style
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        a = letters[(k // 26) % 26]
        b = letters[k % 26]
        num = 100 + (k * 7) % 900
        return f"{a}{b}{num}"

    k = cnt + 1
    while len(rows) < to_add:
        dep = rnd.choice(cities)
        arr = rnd.choice(cities)
        if arr == dep:
            continue
        plane_id = rnd.choice(plane_ids)

        d = start_date + timedelta(days=rnd.randint(0, 365))
        t_h = rnd.choice([6, 8, 10, 12, 14, 16, 18, 20, 22])
        t_m = rnd.choice([0, 15, 30, 45])
        fdate = d.isoformat()
        ftime = f"{t_h:02d}:{t_m:02d}"

        fn = gen_number(k)
        k += 1
        if fn in used_numbers:
            continue
        used_numbers.add(fn)

        rows.append((plane_id, fn, dep, arr, fdate, ftime))

    conn.executemany("""
        INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
        VALUES (?, ?, ?, ?, ?, ?);
    """, rows)

# =========================
# TELEGRAM BOT
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = norm_username(u.username or "")
    if REQUIRE_USERNAME and not username:
        await update.message.reply_text(
            "У тебя не задан @username.\n"
            "Telegram → Settings → Username.\n"
            "Поставь и снова жми /start."
        )
        return

    conn = db_connect()
    try:
        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO tg_users(username, chat_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                chat_id=excluded.chat_id,
                updated_at=excluded.updated_at;
        """, (username, int(chat.id), ts, ts))
        conn.commit()
    finally:
        conn.close()

    await update.message.reply_text(
        "Ок. Я тебя привязала.\n"
        "Теперь коды будут приходить сюда."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("/start — привязать аккаунт\n/help — помощь")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text("Код вводится в веб-приложении. Тут я просто почтальон 😈")
        return
    await update.message.reply_text("Я бот кодов. Жми /start, если ещё нет привязки.")

async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT request_id, username, purpose
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 30;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = norm_username(r["username"])
            purpose = (r["purpose"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?;",
                (username,)
            ).fetchone()
            if not user:
                continue

            code = gen_code()
            title = {
                "register": "Регистрация",
                "login": "Вход",
                "booking": "Бронирование"
            }.get(purpose, purpose)

            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{title}</b>\n\n"
                "Введи этот код в веб-приложении."
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
            SELECT notif_id, username, message
            FROM tg_notifications
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 30;
        """).fetchall()

        for r in rows:
            notif_id = int(r["notif_id"])
            username = norm_username(r["username"])
            message = (r["message"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?;",
                (username,)
            ).fetchone()
            if not user:
                continue

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

async def post_init(app: Application) -> None:
    app.create_task(background_loop(app))

# =========================
# FASTAPI
# =========================

api_app = FastAPI(title="airline-web-tg")

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ReqCode(BaseModel):
    username: str
    purpose: str  # register|login|booking (booking обычно через /booking/request)

class ConfirmRegister(BaseModel):
    username: str
    code: str
    last_name: str
    first_name: str
    middle_name: str | None = None
    passport_no: str
    phone: str
    email: str

class ConfirmLogin(BaseModel):
    username: str
    code: str

class FlightSearch(BaseModel):
    dep: str | None = None
    arr: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = 120

class BookingReq(BaseModel):
    token: str
    flight_id: int
    seat_no: str
    price_usd: float

class BookingConfirm(BaseModel):
    token: str
    request_id: str
    code: str

def must_session(conn: sqlite3.Connection, token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise HTTPException(401, "Нет токена сессии")
    row = conn.execute("SELECT username FROM sessions WHERE token=?;", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "Сессия не найдена. Войди заново.")
    return norm_username(row["username"])

def ensure_tg_bound(conn: sqlite3.Connection, username: str) -> None:
    row = conn.execute("SELECT 1 FROM tg_users WHERE username=?;", (username,)).fetchone()
    if not row:
        raise HTTPException(400, "Открой бота и нажми /start — иначе я не могу прислать код.")

@api_app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH)}

@api_app.post("/api/auth/request-code")
def api_auth_request_code(req: ReqCode):
    username = norm_username(req.username)
    purpose = (req.purpose or "").strip().lower()

    if not username:
        raise HTTPException(400, "Нет @username")
    if purpose not in ("register", "login"):
        raise HTTPException(400, "purpose должен быть register или login")

    conn = db_connect()
    try:
        ensure_tg_bound(conn, username)

        rid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, status, payload, created_at)
            VALUES (?, ?, ?, 'pending', NULL, ?);
        """, (rid, username, purpose, now_utc_iso()))
        conn.commit()
        return {"request_id": rid}
    finally:
        conn.close()

def consume_code(conn: sqlite3.Connection, username: str, purpose: str, code: str) -> None:
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(400, "Код — 6 цифр")

    row = conn.execute("""
        SELECT request_id, code AS real_code, status
        FROM tg_code_requests
        WHERE username=? AND purpose=?
        ORDER BY created_at DESC
        LIMIT 1;
    """, (username, purpose)).fetchone()

    if not row:
        raise HTTPException(400, "Нет запроса на код")
    if row["status"] == "pending":
        raise HTTPException(400, "Код ещё не отправлен ботом")
    if row["status"] != "sent":
        raise HTTPException(400, "Код уже использован/отменён")
    if (row["real_code"] or "").strip() != code:
        raise HTTPException(400, "Неверный код")

    conn.execute("""
        UPDATE tg_code_requests
        SET status='used', used_at=?
        WHERE request_id=?;
    """, (now_utc_iso(), row["request_id"]))

@api_app.post("/api/auth/confirm-register")
def api_auth_confirm_register(req: ConfirmRegister):
    username = norm_username(req.username)

    if not username:
        raise HTTPException(400, "Нет @username")

    # basic fields
    last_name = (req.last_name or "").strip()
    first_name = (req.first_name or "").strip()
    passport_no = (req.passport_no or "").strip()
    phone = (req.phone or "").strip()
    email = (req.email or "").strip()
    middle = (req.middle_name or "").strip() or None

    if not last_name or not first_name or not passport_no or not phone or not email:
        raise HTTPException(400, "Заполни обязательные поля")

    conn = db_connect()
    try:
        ensure_tg_bound(conn, username)
        consume_code(conn, username, "register", req.code)

        conn.execute("""
            INSERT INTO passengers(passenger_id, last_name, first_name, middle_name, passport_no, phone, email)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(passenger_id) DO UPDATE SET
                last_name=excluded.last_name,
                first_name=excluded.first_name,
                middle_name=excluded.middle_name,
                passport_no=excluded.passport_no,
                phone=excluded.phone,
                email=excluded.email;
        """, (username, last_name, first_name, middle, passport_no, phone, email))

        token = str(uuid.uuid4())
        conn.execute("INSERT INTO sessions(token, username, created_at) VALUES (?, ?, ?);",
                     (token, username, now_utc_iso()))
        conn.commit()

        return {"token": token}
    finally:
        conn.close()

@api_app.post("/api/auth/confirm-login")
def api_auth_confirm_login(req: ConfirmLogin):
    username = norm_username(req.username)
    if not username:
        raise HTTPException(400, "Нет @username")

    conn = db_connect()
    try:
        ensure_tg_bound(conn, username)
        consume_code(conn, username, "login", req.code)

        p = conn.execute("SELECT 1 FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
        if not p:
            raise HTTPException(404, "Пользователь не зарегистрирован")

        token = str(uuid.uuid4())
        conn.execute("INSERT INTO sessions(token, username, created_at) VALUES (?, ?, ?);",
                     (token, username, now_utc_iso()))
        conn.commit()
        return {"token": token}
    finally:
        conn.close()

@api_app.post("/api/flights/search")
def api_flights_search(req: FlightSearch):
    conn = db_connect()
    try:
        seed_flights_if_needed(conn, target=300)

        dep = (req.dep or "").strip()
        arr = (req.arr or "").strip()
        df = (req.date_from or "").strip()
        dt = (req.date_to or "").strip()
        limit = max(1, min(int(req.limit or 120), 500))

        where = []
        args = []

        if dep:
            where.append("f.departure_city LIKE ?")
            args.append(f"%{dep}%")
        if arr:
            where.append("f.arrival_city LIKE ?")
            args.append(f"%{arr}%")
        if df:
            where.append("f.flight_date >= ?")
            args.append(df)
        if dt:
            where.append("f.flight_date <= ?")
            args.append(dt)

        wsql = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(f"""
            SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            {wsql}
            ORDER BY f.flight_date, f.flight_time
            LIMIT ?;
        """, (*args, limit)).fetchall()

        flights = []
        for r in rows:
            fid = int(r["flight_id"])
            flights.append({
                "flight_id": fid,
                "flight_number": r["flight_number"],
                "dep": r["departure_city"],
                "arr": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "plane_model": r["plane_model"],
                "seat_capacity": int(r["seat_capacity"]),
                "suggested_price": stable_price(fid)
            })

        return {"flights": flights}
    finally:
        conn.close()

@api_app.get("/api/flights/{flight_id}/seats")
def api_flight_seats(flight_id: int):
    conn = db_connect()
    try:
        row = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (int(flight_id),)).fetchone()
        if not row:
            raise HTTPException(404, "Рейс не найден")

        capacity = int(row["seat_capacity"])
        all_seats = seats_for_capacity(capacity)

        booked = {
            r["seat_no"] for r in conn.execute("""
                SELECT seat_no
                FROM tickets
                WHERE flight_id=?;
            """, (int(flight_id),)).fetchall()
        }

        seats = [{"seat": s, "status": ("booked" if s in booked else "free")} for s in all_seats]
        return {"seats": seats, "capacity": capacity}
    finally:
        conn.close()

@api_app.post("/api/booking/request")
def api_booking_request(req: BookingReq):
    conn = db_connect()
    try:
        username = must_session(conn, req.token)
        ensure_tg_bound(conn, username)

        flight_id = int(req.flight_id)
        seat_no = (req.seat_no or "").strip().upper()
        price = float(req.price_usd)

        if not seat_no:
            raise HTTPException(400, "Нет места")
        if not (price > 0):
            raise HTTPException(400, "Цена должна быть > 0")

        frow = conn.execute("""
            SELECT p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()
        if not frow:
            raise HTTPException(404, "Рейс не найден")

        capacity = int(frow["seat_capacity"])
        valid = set(seats_for_capacity(capacity))
        if seat_no not in valid:
            raise HTTPException(400, "Некорректное место для этого самолёта")

        exists = conn.execute("""
            SELECT 1 FROM tickets WHERE flight_id=? AND seat_no=? LIMIT 1;
        """, (flight_id, seat_no)).fetchone()
        if exists:
            raise HTTPException(409, "Это место уже занято")

        rid = str(uuid.uuid4())
        payload = json.dumps({"flight_id": flight_id, "seat_no": seat_no, "price_usd": price}, ensure_ascii=False)

        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, status, payload, created_at)
            VALUES (?, ?, 'booking', 'pending', ?, ?);
        """, (rid, username, payload, now_utc_iso()))
        conn.commit()

        return {"request_id": rid}
    finally:
        conn.close()

@api_app.post("/api/booking/confirm")
def api_booking_confirm(req: BookingConfirm):
    conn = db_connect()
    try:
        username = must_session(conn, req.token)

        rid = (req.request_id or "").strip()
        code = (req.code or "").strip()
        if not rid:
            raise HTTPException(400, "Нет request_id")
        if not re.fullmatch(r"\d{6}", code):
            raise HTTPException(400, "Код — 6 цифр")

        row = conn.execute("""
            SELECT request_id, code AS real_code, status, payload
            FROM tg_code_requests
            WHERE request_id=? AND username=? AND purpose='booking'
            LIMIT 1;
        """, (rid, username)).fetchone()

        if not row:
            raise HTTPException(404, "Запрос бронирования не найден")
        if row["status"] == "pending":
            raise HTTPException(400, "Код ещё не отправлен ботом")
        if row["status"] != "sent":
            raise HTTPException(400, "Запрос уже использован/отменён")
        if (row["real_code"] or "").strip() != code:
            raise HTTPException(400, "Неверный код")

        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}

        flight_id = int(payload.get("flight_id", 0))
        seat_no = str(payload.get("seat_no", "")).strip().upper()
        price = float(payload.get("price_usd", 0.0))

        if not flight_id or not seat_no or not (price > 0):
            raise HTTPException(400, "Битый payload брони")

        status_id = get_status_id(conn, "BOOKED")

        # транзакция: ещё раз проверяем место и вставляем
        conn.execute("BEGIN;")
        exists = conn.execute("""
            SELECT 1 FROM tickets WHERE flight_id=? AND seat_no=? LIMIT 1;
        """, (flight_id, seat_no)).fetchone()
        if exists:
            conn.execute("ROLLBACK;")
            raise HTTPException(409, "Это место уже занято")

        conn.execute("""
            INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd)
            VALUES (?, ?, ?, ?, ?);
        """, (flight_id, username, status_id, seat_no, price))

        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (now_utc_iso(), rid))

        # уведомление в TG (приятно же)
        f = conn.execute("""
            SELECT f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()

        if f:
            msg = (
                "✅ <b>Бронь подтверждена</b>\n\n"
                f"Рейс: <b>{f['flight_number']}</b>\n"
                f"{f['departure_city']} → {f['arrival_city']}\n"
                f"{f['flight_date']} {f['flight_time']} · {f['plane_model']}\n"
                f"Место: <b>{seat_no}</b>\n"
                f"Цена: <b>${price:.2f}</b>"
            )
            conn.execute("""
                INSERT INTO tg_notifications(username, message, status, created_at)
                VALUES (?, ?, 'pending', ?);
            """, (username, msg, now_utc_iso()))

        conn.execute("COMMIT;")
        return {"ok": True}
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise HTTPException(409, "Это место уже занято")
    finally:
        conn.close()

@api_app.get("/api/me/flights")
def api_me_flights(token: str):
    conn = db_connect()
    try:
        username = must_session(conn, token)

        rows = conn.execute("""
            SELECT t.ticket_id, t.seat_no, t.price_usd,
                   f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model, p.seat_capacity
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE t.passenger_id=?
            ORDER BY f.flight_date, f.flight_time;
        """, (username,)).fetchall()

        out = []
        for r in rows:
            out.append({
                "ticket_id": int(r["ticket_id"]),
                "seat_no": r["seat_no"],
                "price_usd": float(r["price_usd"]),
                "flight_id": int(r["flight_id"]),
                "flight_number": r["flight_number"],
                "dep": r["departure_city"],
                "arr": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "plane_model": r["plane_model"],
                "seat_capacity": int(r["seat_capacity"]),
            })
        return {"flights": out}
    finally:
        conn.close()

# =========================
# RUNNERS
# =========================

def run_api() -> None:
    cfg = uvicorn.Config(api_app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(cfg)
    server.run()

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой.\n"
            "Запусти так:\n"
            "  set BOT_TOKEN=... && py -3 bot\\botinok.py"
        )

    db_init()
    print(f"[bot] DB: {DB_PATH}")
    print(f"[api] http://{API_HOST}:{API_PORT}")

    # API in background thread
    th = threading.Thread(target=run_api, daemon=True)
    th.start()

    # Telegram bot
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
