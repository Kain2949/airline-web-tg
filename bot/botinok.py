import os
import re
import json
import uuid
import time
import random
import sqlite3
import asyncio
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
DEFAULT_DB_PATH = PROJECT_ROOT / "airline_lab.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "1488"))

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "1.5"))
CODE_TTL_SECONDS = int(os.getenv("CODE_TTL_SECONDS", "600"))  # 10 минут

REQUIRE_USERNAME = True

STATUS_BOOKED = "booked"


# =========================
# HELPERS
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

def is_code(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", (s or "").strip()))

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def table_columns(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return {r["name"] for r in rows}

def seat_row_labels(n_rows: int) -> list:
    # A..Z, AA..AZ, BA.. etc
    labels = []
    i = 0
    while len(labels) < n_rows:
        x = i
        s = ""
        while True:
            s = chr(ord('A') + (x % 26)) + s
            x = x // 26 - 1
            if x < 0:
                break
        labels.append(s)
        i += 1
    return labels

def build_seats(capacity: int) -> list:
    # 6 мест в ряду: 1..6, ряды буквами (A, B, C...)
    # 60 -> 10 рядов, 120 -> 20, 180 -> 30
    n_rows = capacity // 6
    rows = seat_row_labels(n_rows)
    seats = []
    for r in rows:
        for n in range(1, 7):
            seats.append(f"{r}{n}")
    return seats

def ensure_schema_and_seed() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # --- Telegram users (chat_id привязка) ---
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

        # --- Code requests ---
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_code_requests (
            request_id  TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            purpose     TEXT NOT NULL,   -- 'register' | 'login' | 'booking'
            code        TEXT,
            status      TEXT NOT NULL,   -- 'pending' | 'sent' | 'used' | 'cancelled'
            payload     TEXT,            -- JSON
            created_at  TEXT NOT NULL,
            sent_at     TEXT,
            used_at     TEXT
        );
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_code_pending
        ON tg_code_requests(status, created_at);
        """)

        # миграции если у тебя старая таблица без нужных колонок
        cols = table_columns(conn, "tg_code_requests")
        if "purpose" not in cols:
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN purpose TEXT;")
        if "payload" not in cols:
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN payload TEXT;")
        if "sent_at" not in cols:
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN sent_at TEXT;")
        if "used_at" not in cols:
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN used_at TEXT;")

        # --- Sessions ---
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);")

        # --- Airline schema (под твою картинку, но passenger_id = telegram tag) ---
        conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_statuses (
            status_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            status_name TEXT NOT NULL UNIQUE
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS planes (
            plane_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            model             TEXT NOT NULL,
            manufacture_year  INTEGER NOT NULL,
            seat_capacity     INTEGER NOT NULL
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            flight_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            plane_id         INTEGER NOT NULL,
            flight_number    TEXT NOT NULL,
            departure_city   TEXT NOT NULL,
            arrival_city     TEXT NOT NULL,
            flight_date      TEXT NOT NULL,  -- YYYY-MM-DD
            flight_time      TEXT NOT NULL,  -- HH:MM
            FOREIGN KEY (plane_id) REFERENCES planes(plane_id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id  TEXT PRIMARY KEY, -- Telegram @username
            last_name     TEXT NOT NULL,
            first_name    TEXT NOT NULL,
            middle_name   TEXT,
            passport_no   TEXT NOT NULL,
            phone         TEXT NOT NULL,
            email         TEXT NOT NULL
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id     INTEGER NOT NULL,
            passenger_id  TEXT NOT NULL,      -- Telegram @username
            status_id     INTEGER NOT NULL,
            seat_no       TEXT NOT NULL,
            price_usd     REAL NOT NULL,
            FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
            FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
            FOREIGN KEY (status_id) REFERENCES ticket_statuses(status_id)
        );
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_flights_date ON flights(flight_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flights_plane ON flights(plane_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flights_route_date ON flights(departure_city, arrival_city, flight_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_passengers_last ON passengers(last_name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_flight ON tickets(flight_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_pass_stat ON tickets(passenger_id, status_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status_id);")

        # Жёстко запрещаем двойную бронь одного места на одном рейсе
        conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_seat
        ON tickets(flight_id, seat_no);
        """)

        # seed statuses
        row = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (STATUS_BOOKED,)).fetchone()
        if not row:
            conn.execute("INSERT INTO ticket_statuses(status_name) VALUES (?);", (STATUS_BOOKED,))

        # seed planes
        existing_planes = conn.execute("SELECT COUNT(*) AS c FROM planes;").fetchone()["c"]
        if existing_planes == 0:
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES (?,?,?);", ("Airbus A319", 2012, 60))
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES (?,?,?);", ("Boeing 737-800", 2016, 120))
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES (?,?,?);", ("Airbus A321", 2019, 180))

        # seed flights (100-500 — сделаем 300)
        existing_flights = conn.execute("SELECT COUNT(*) AS c FROM flights;").fetchone()["c"]
        if existing_flights < 100:
            # если уже что-то было — не дублим бесконечно: подчистим и заново нормально зальём
            conn.execute("DELETE FROM flights;")

            cities = [
                "Minsk, BY", "Warsaw, PL", "Krakow, PL", "Gdansk, PL", "Vilnius, LT",
                "Riga, LV", "Berlin, DE", "Prague, CZ", "Vienna, AT", "Budapest, HU",
                "Paris, FR", "Rome, IT", "Barcelona, ES", "Madrid, ES", "London, UK",
                "Dublin, IE", "Oslo, NO", "Stockholm, SE", "Helsinki, FI", "Zurich, CH"
            ]
            plane_ids = [r["plane_id"] for r in conn.execute("SELECT plane_id FROM planes;").fetchall()]

            base_date = datetime.now().date()
            for i in range(300):
                dep, arr = random.sample(cities, 2)
                d = base_date + timedelta(days=random.randint(0, 180))
                hh = random.choice([6, 8, 10, 12, 14, 16, 18, 20, 22])
                mm = random.choice([0, 15, 30, 45])
                t = f"{hh:02d}:{mm:02d}"
                plane_id = random.choice(plane_ids)
                fn = f"AB{random.randint(100, 999)}"
                conn.execute("""
                    INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
                    VALUES (?,?,?,?,?,?);
                """, (plane_id, fn, dep, arr, str(d), t))

        conn.commit()
    finally:
        conn.close()


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
            "Настройки → Username → задай и снова нажми /start."
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
        "Ок. Я тебя привязала ✅\n"
        "Теперь коды подтверждения будут прилетать сюда."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать Telegram\n"
        "/help — помощь\n\n"
        "Коды для входа/регистрации/брони приходят автоматически."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if is_code(txt):
        await update.message.reply_text("Код вводится в веб-приложении. Тут — не надо 😈")
        return
    await update.message.reply_text("Я бот подтверждений. Нажми /start, если ещё не привязан.")


# =========================
# BACKGROUND: SEND CODES
# =========================

async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT request_id, username, purpose, payload, created_at
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 30;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            purpose = (r["purpose"] or "").strip()

            user = conn.execute("SELECT chat_id FROM tg_users WHERE username=?;", (username,)).fetchone()
            if not user:
                # пока не нажал /start
                continue

            code = gen_code()
            purpose_name = {
                "register": "Регистрация",
                "login": "Вход",
                "booking": "Бронирование"
            }.get(purpose, purpose)

            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{purpose_name}</b>\n\n"
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

async def background_loop(app: Application) -> None:
    while True:
        try:
            await process_pending_codes(app)
        except Exception:
            pass
        await asyncio.sleep(POLL_SECONDS)


# =========================
# FASTAPI
# =========================

api = FastAPI(title="airline api")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

def require_session(conn: sqlite3.Connection, token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise HTTPException(401, "no session")
    row = conn.execute("SELECT username, expires_at FROM sessions WHERE token=?;", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "bad session")
    exp = datetime.fromisoformat(row["expires_at"])
    if exp < datetime.now(timezone.utc):
        raise HTTPException(401, "session expired")
    return row["username"]

class ReqCode(BaseModel):
    username: str
    purpose: str  # register | login | booking
    payload: dict | None = None

class ConfirmLogin(BaseModel):
    username: str
    code: str

class ConfirmRegister(BaseModel):
    username: str
    code: str
    last_name: str
    first_name: str
    middle_name: str | None = None
    passport_no: str
    phone: str
    email: str

class SearchFlights(BaseModel):
    dep: str | None = None
    arr: str | None = None
    date_from: str | None = None  # YYYY-MM-DD
    date_to: str | None = None    # YYYY-MM-DD
    limit: int | None = 80

class SeatsReq(BaseModel):
    token: str
    flight_id: int

class BookingStart(BaseModel):
    token: str
    flight_id: int
    seat_no: str
    price_usd: float

class BookingConfirm(BaseModel):
    token: str
    request_id: str
    code: str

@api.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH)}

def create_code_request(conn: sqlite3.Connection, username: str, purpose: str, payload: dict | None) -> str:
    username = normalize_username(username)
    if not username:
        raise HTTPException(400, "bad username")

    rid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO tg_code_requests(request_id, username, purpose, code, status, payload, created_at)
        VALUES (?,?,?,?,?,?,?);
    """, (rid, username, purpose, None, "pending", json.dumps(payload or {}, ensure_ascii=False), now_utc_iso()))
    conn.commit()
    return rid

def verify_code(conn: sqlite3.Connection, request_id: str, code: str, purpose: str) -> str:
    request_id = (request_id or "").strip()
    code = (code or "").strip()
    if not request_id or not is_code(code):
        raise HTTPException(400, "bad request_id/code")

    row = conn.execute("""
        SELECT username, purpose, code, status, created_at
        FROM tg_code_requests
        WHERE request_id=?;
    """, (request_id,)).fetchone()
    if not row:
        raise HTTPException(404, "request not found")

    if (row["purpose"] or "") != purpose:
        raise HTTPException(400, "purpose mismatch")

    if row["status"] != "sent":
        raise HTTPException(400, "code not sent yet")

    created = datetime.fromisoformat(row["created_at"])
    if created.replace(tzinfo=timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS) < datetime.now(timezone.utc):
        raise HTTPException(400, "code expired")

    if (row["code"] or "") != code:
        raise HTTPException(400, "wrong code")

    conn.execute("""
        UPDATE tg_code_requests
        SET status='used', used_at=?
        WHERE request_id=?;
    """, (now_utc_iso(), request_id))
    conn.commit()

    return normalize_username(row["username"])

@api.post("/api/auth/request-code")
def auth_request_code(body: ReqCode):
    purpose = (body.purpose or "").strip()
    if purpose not in ("register", "login", "booking"):
        raise HTTPException(400, "bad purpose")

    username = normalize_username(body.username)

    conn = db_connect()
    try:
        # если login/booking — пользователь должен существовать
        if purpose in ("login", "booking"):
            p = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
            if not p:
                raise HTTPException(404, "user not registered")

        # booking — валидируем payload чуть-чуть
        payload = body.payload or {}
        if purpose == "booking":
            if not payload.get("flight_id") or not payload.get("seat_no"):
                raise HTTPException(400, "booking payload required")

        rid = create_code_request(conn, username, purpose, payload)
        return {"ok": True, "request_id": rid}
    finally:
        conn.close()

@api.post("/api/auth/confirm-login")
def auth_confirm_login(body: ConfirmLogin):
    username = normalize_username(body.username)

    conn = db_connect()
    try:
        # найдём последний request_id для login по username со статусом sent, если клиент не хранит — но клиент хранит
        # тут проще: клиент всегда присылает request_id в confirm — но ты хотел «простое»
        # поэтому сделаем по-людски: ищем самый свежий sent login-request и сверяем код.
        row = conn.execute("""
            SELECT request_id
            FROM tg_code_requests
            WHERE username=? AND purpose='login' AND status='sent'
            ORDER BY created_at DESC
            LIMIT 1;
        """, (username,)).fetchone()
        if not row:
            raise HTTPException(400, "no login request")

        _ = verify_code(conn, row["request_id"], body.code, "login")

        # session
        token = str(uuid.uuid4())
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        conn.execute("""
            INSERT INTO sessions(token, username, created_at, expires_at)
            VALUES (?,?,?,?);
        """, (token, username, now_utc_iso(), exp.replace(microsecond=0).isoformat()))
        conn.commit()

        return {"ok": True, "token": token}
    finally:
        conn.close()

@api.post("/api/auth/confirm-register")
def auth_confirm_register(body: ConfirmRegister):
    username = normalize_username(body.username)

    conn = db_connect()
    try:
        row = conn.execute("""
            SELECT request_id
            FROM tg_code_requests
            WHERE username=? AND purpose='register' AND status='sent'
            ORDER BY created_at DESC
            LIMIT 1;
        """, (username,)).fetchone()
        if not row:
            raise HTTPException(400, "no register request")

        _ = verify_code(conn, row["request_id"], body.code, "register")

        # upsert passenger
        conn.execute("""
            INSERT INTO passengers(passenger_id, last_name, first_name, middle_name, passport_no, phone, email)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(passenger_id) DO UPDATE SET
                last_name=excluded.last_name,
                first_name=excluded.first_name,
                middle_name=excluded.middle_name,
                passport_no=excluded.passport_no,
                phone=excluded.phone,
                email=excluded.email;
        """, (
            username,
            body.last_name.strip(),
            body.first_name.strip(),
            (body.middle_name or "").strip() or None,
            body.passport_no.strip(),
            body.phone.strip(),
            body.email.strip()
        ))

        # session
        token = str(uuid.uuid4())
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        conn.execute("""
            INSERT INTO sessions(token, username, created_at, expires_at)
            VALUES (?,?,?,?);
        """, (token, username, now_utc_iso(), exp.replace(microsecond=0).isoformat()))

        conn.commit()
        return {"ok": True, "token": token}
    finally:
        conn.close()

@api.post("/api/flights/search")
def flights_search(body: SearchFlights):
    dep = (body.dep or "").strip()
    arr = (body.arr or "").strip()
    date_from = (body.date_from or "").strip()
    date_to = (body.date_to or "").strip()
    limit = int(body.limit or 80)
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    where = []
    params = []

    if dep:
        where.append("departure_city LIKE ?")
        params.append(f"%{dep}%")
    if arr:
        where.append("arrival_city LIKE ?")
        params.append(f"%{arr}%")
    if date_from:
        where.append("flight_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("flight_date <= ?")
        params.append(date_to)

    sql = """
        SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
               p.model, p.seat_capacity
        FROM flights f
        JOIN planes p ON p.plane_id = f.plane_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY f.flight_date ASC, f.flight_time ASC LIMIT ?;"
    params.append(limit)

    conn = db_connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            # “цена” чисто для карточки, реальная фиксируется в tickets.price_usd
            base = 70 + (r["seat_capacity"] // 3)
            price = round(base + random.randint(0, 220), 2)
            out.append({
                "flight_id": r["flight_id"],
                "flight_number": r["flight_number"],
                "dep": r["departure_city"],
                "arr": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "plane_model": r["model"],
                "seat_capacity": r["seat_capacity"],
                "suggested_price": price
            })
        return {"ok": True, "flights": out}
    finally:
        conn.close()

@api.get("/api/flights/{flight_id}/seats")
def flight_seats(flight_id: int):
    conn = db_connect()
    try:
        f = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()
        if not f:
            raise HTTPException(404, "flight not found")

        seat_capacity = int(f["seat_capacity"])
        all_seats = build_seats(seat_capacity)

        booked_id = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (STATUS_BOOKED,)).fetchone()["status_id"]
        taken = conn.execute("""
            SELECT seat_no
            FROM tickets
            WHERE flight_id=? AND status_id=?;
        """, (flight_id, booked_id)).fetchall()
        taken_set = {r["seat_no"] for r in taken}

        seats = [{"seat": s, "status": ("booked" if s in taken_set else "free")} for s in all_seats]
        return {"ok": True, "seat_capacity": seat_capacity, "seats": seats}
    finally:
        conn.close()

@api.post("/api/booking/request")
def booking_request(body: BookingStart):
    conn = db_connect()
    try:
        username = require_session(conn, body.token)

        # seat format
        seat_no = (body.seat_no or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{1,3}[1-6]", seat_no):
            raise HTTPException(400, "bad seat")

        # check flight exists
        f = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (body.flight_id,)).fetchone()
        if not f:
            raise HTTPException(404, "flight not found")

        # check seat in plane
        all_seats = set(build_seats(int(f["seat_capacity"])))
        if seat_no not in all_seats:
            raise HTTPException(400, "seat not in this plane")

        # check already taken
        booked_id = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (STATUS_BOOKED,)).fetchone()["status_id"]
        taken = conn.execute("""
            SELECT 1 FROM tickets
            WHERE flight_id=? AND seat_no=? AND status_id=?;
        """, (body.flight_id, seat_no, booked_id)).fetchone()
        if taken:
            raise HTTPException(409, "seat already booked")

        payload = {
            "flight_id": int(body.flight_id),
            "seat_no": seat_no,
            "price_usd": float(body.price_usd)
        }
        rid = create_code_request(conn, username, "booking", payload)
        return {"ok": True, "request_id": rid}
    finally:
        conn.close()

@api.post("/api/booking/confirm")
def booking_confirm(body: BookingConfirm):
    conn = db_connect()
    try:
        username = require_session(conn, body.token)

        # validate code for booking request_id exactly
        # (а не “последний”) — чтобы ты не ловил чужую путаницу
        row = conn.execute("""
            SELECT payload
            FROM tg_code_requests
            WHERE request_id=? AND username=?;
        """, (body.request_id, username)).fetchone()
        if not row:
            raise HTTPException(404, "request not found")

        _ = verify_code(conn, body.request_id, body.code, "booking")

        payload = {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}

        flight_id = int(payload.get("flight_id") or 0)
        seat_no = (payload.get("seat_no") or "").strip().upper()
        price_usd = float(payload.get("price_usd") or 0.0)

        if not flight_id or not seat_no or price_usd <= 0:
            raise HTTPException(400, "bad payload")

        booked_id = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (STATUS_BOOKED,)).fetchone()["status_id"]

        # re-check seat free (race condition)
        taken = conn.execute("""
            SELECT 1 FROM tickets
            WHERE flight_id=? AND seat_no=?;
        """, (flight_id, seat_no)).fetchone()
        if taken:
            raise HTTPException(409, "seat already booked")

        # passenger must exist
        p = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
        if not p:
            raise HTTPException(404, "user not registered")

        conn.execute("""
            INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd)
            VALUES (?,?,?,?,?);
        """, (flight_id, username, booked_id, seat_no, price_usd))
        conn.commit()

        return {"ok": True}
    finally:
        conn.close()

@api.get("/api/me/flights")
def my_flights(token: str):
    conn = db_connect()
    try:
        username = require_session(conn, token)

        rows = conn.execute("""
            SELECT t.ticket_id, t.seat_no, t.price_usd,
                   f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.model AS plane_model
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE t.passenger_id=?
            ORDER BY f.flight_date ASC, f.flight_time ASC;
        """, (username,)).fetchall()

        out = []
        for r in rows:
            out.append({
                "ticket_id": r["ticket_id"],
                "seat_no": r["seat_no"],
                "price_usd": r["price_usd"],
                "flight_number": r["flight_number"],
                "dep": r["departure_city"],
                "arr": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "plane_model": r["plane_model"]
            })
        return {"ok": True, "flights": out}
    finally:
        conn.close()


# =========================
# RUNNERS
# =========================

def run_api() -> None:
    print(f"[api] http://{API_HOST}:{API_PORT}")
    uvicorn.run(api, host=API_HOST, port=API_PORT, log_level="info")

def run_bot() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой.\n"
            "Запускай как: set BOT_TOKEN=... && py -3 bot\\botinok.py"
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    async def _post_init(application: Application):
        application.create_task(background_loop(application))

    app.post_init = _post_init  # нормальный запуск таска, без твоего PTB нытья

    print(f"[bot] DB: {DB_PATH}")
    print("[bot] starting...")

    app.run_polling(allowed_updates=Update.ALL_TYPES)

def main() -> None:
    ensure_schema_and_seed()

    # API в отдельном потоке
    t = threading.Thread(target=run_api, daemon=True)
    t.start()

    # бот в главном
    run_bot()

if __name__ == "__main__":
    main()
