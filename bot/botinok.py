import os
import re
import json
import time
import uuid
import random
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, date

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

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

# polling DB for pending code-requests
POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "2.0"))

REQUIRE_USERNAME = True  # require telegram username for /start mapping

# =========================
# DB
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def normalize_username(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if not s.startswith("@"):
        s = "@" + s
    return s.lower()

def gen_code() -> str:
    return f"{random.randint(0, 999999):06d}"

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect()
    try:
        # ---- telegram mapping ----
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
            purpose     TEXT NOT NULL,   -- 'register' | 'login' | 'booking'
            code        TEXT,
            status      TEXT NOT NULL,   -- 'pending' | 'sent' | 'used' | 'cancelled'
            payload     TEXT,            -- JSON string
            created_at  TEXT NOT NULL,
            sent_at     TEXT,
            used_at     TEXT
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_code_pending ON tg_code_requests(status, created_at);")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_notifications (
            notif_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,
            message     TEXT,
            payload     TEXT,
            status      TEXT NOT NULL, -- pending/sent
            created_at  TEXT NOT NULL,
            sent_at     TEXT
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_notif_pending ON tg_notifications(status, created_at);")

        # ---- airline domain ----
        conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_statuses (
            status_id   INTEGER PRIMARY KEY,
            status_name TEXT NOT NULL UNIQUE
        );
        """)

        # planes: seat_capacity only 60/120/180
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
            flight_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            plane_id        INTEGER NOT NULL,
            flight_number   TEXT NOT NULL,
            departure_city  TEXT NOT NULL,
            arrival_city    TEXT NOT NULL,
            flight_date     TEXT NOT NULL,  -- YYYY-MM-DD
            flight_time     TEXT NOT NULL,  -- HH:MM
            FOREIGN KEY(plane_id) REFERENCES planes(plane_id)
        );
        """)

        # IMPORTANT: passenger_id is telegram username (TEXT)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id  TEXT PRIMARY KEY,  -- '@username'
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
            passenger_id  TEXT NOT NULL,      -- '@username'
            status_id     INTEGER NOT NULL,
            seat_no       TEXT NOT NULL,      -- A1..F30
            price_usd     REAL NOT NULL,
            created_at    TEXT NOT NULL,
            FOREIGN KEY(flight_id) REFERENCES flights(flight_id),
            FOREIGN KEY(passenger_id) REFERENCES passengers(passenger_id),
            FOREIGN KEY(status_id) REFERENCES ticket_statuses(status_id),
            UNIQUE(flight_id, seat_no)
        );
        """)

        conn.commit()

        # seed statuses
        conn.execute("INSERT OR IGNORE INTO ticket_statuses(status_id, status_name) VALUES (1,'reserved');")
        conn.execute("INSERT OR IGNORE INTO ticket_statuses(status_id, status_name) VALUES (2,'purchased');")
        conn.commit()

        # seed planes if empty
        cnt_planes = conn.execute("SELECT COUNT(*) AS c FROM planes;").fetchone()["c"]
        if cnt_planes == 0:
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Airbus A60', 2014, 60);")
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Boeing B120', 2017, 120);")
            conn.execute("INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES ('Boeing B180', 2019, 180);")
            conn.commit()

        # seed flights if empty
        cnt_flights = conn.execute("SELECT COUNT(*) AS c FROM flights;").fetchone()["c"]
        if cnt_flights == 0:
            plane_ids = [r["plane_id"] for r in conn.execute("SELECT plane_id FROM planes ORDER BY plane_id;").fetchall()]
            # простая “демка” на ближайшие 10 дней
            cities = [
                ("Minsk", "Warsaw"),
                ("Warsaw", "Minsk"),
                ("Minsk", "Vilnius"),
                ("Vilnius", "Minsk"),
                ("Warsaw", "Berlin"),
                ("Berlin", "Warsaw"),
            ]
            today = date.today()
            n = 1
            for d in range(0, 10):
                dt = today.fromordinal(today.toordinal() + d).isoformat()
                for (a, b) in cities:
                    plane_id = random.choice(plane_ids)
                    hh = random.choice(["07:20","10:10","13:45","16:30","19:05","21:40"])
                    fn = f"SU-{100+n}"
                    n += 1
                    conn.execute("""
                        INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
                        VALUES(?,?,?,?,?,?);
                    """, (plane_id, fn, a, b, dt, hh))
            conn.commit()

    finally:
        conn.close()

def seat_list_for_capacity(cap: int) -> list[str]:
    # 60/120/180 => 6 letters (A..F) x numbers (10/20/30)
    if cap not in (60, 120, 180):
        raise ValueError("seat_capacity must be 60/120/180")
    nums = cap // 6
    letters = ["A","B","C","D","E","F"]
    seats = []
    for num in range(1, nums + 1):
        for letter in letters:
            seats.append(f"{letter}{num}")
    return seats

# =========================
# BOT PART
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = normalize_username(u.username or "")
    if REQUIRE_USERNAME and not username:
        await update.message.reply_text(
            "У тебя не задан @username в Telegram.\n"
            "Настройки → Username → поставь, потом снова /start.",
        )
        return

    conn = db_connect()
    try:
        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO tg_users(username, chat_id, first_name, last_name, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
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
        "Теперь иди в веб-приложение и запрашивай коды подтверждения.",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать аккаунт Telegram\n"
        "/help — помощь\n\n"
        "Коды приходят сюда автоматически, когда ты жмёшь кнопки в веб-приложении."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text("Код вводи в веб-приложении. Тут я его не принимаю 😈")
        return
    await update.message.reply_text("Я бот кодов. Жми /start, если ещё не привязан.")

async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT request_id, username, purpose
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 20;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            purpose = (r["purpose"] or "").strip()

            user = conn.execute("SELECT chat_id FROM tg_users WHERE username=?;", (username,)).fetchone()
            if not user:
                continue  # user didn't /start

            code = gen_code()

            title = {"register": "Регистрация", "login": "Вход", "booking": "Бронирование"}.get(purpose, purpose)
            msg = (
                f"Код подтверждения: <b>{code}</b>\n"
                f"Тип: <b>{title}</b>\n\n"
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

async def post_init(app: Application) -> None:
    # will warn in PTB sometimes, but works fine
    import asyncio
    app.create_task(background_loop(app))

# =========================
# API (FastAPI)
# =========================

app_api = FastAPI(title="Airline API", version="1.0")

app_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuthStartIn(BaseModel):
    username: str
    purpose: str  # register|login|booking
    payload: dict | None = None

class AuthStartOut(BaseModel):
    request_id: str
    status: str

class AuthVerifyIn(BaseModel):
    username: str
    purpose: str
    code: str

class PassengerIn(BaseModel):
    username: str
    last_name: str
    first_name: str
    middle_name: str | None = ""
    passport_no: str
    phone: str
    email: str

class FlightsSearchIn(BaseModel):
    from_city: str
    to_city: str
    date_out: str          # YYYY-MM-DD
    date_back: str | None = ""  # optional or ""

class BookingStartIn(BaseModel):
    username: str
    flight_id: int
    seat_no: str
    price_usd: float

def get_latest_sent_code(conn, username: str, purpose: str):
    return conn.execute("""
        SELECT request_id, code, status, payload
        FROM tg_code_requests
        WHERE username=? AND purpose=? AND status='sent'
        ORDER BY sent_at DESC
        LIMIT 1;
    """, (username, purpose)).fetchone()

def ensure_passenger_exists(conn, username: str) -> None:
    r = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
    if not r:
        raise HTTPException(status_code=400, detail="Пассажир не зарегистрирован")

@app_api.post("/api/auth/start", response_model=AuthStartOut)
def api_auth_start(inp: AuthStartIn):
    username = normalize_username(inp.username)
    purpose = (inp.purpose or "").strip().lower()
    if purpose not in ("register", "login", "booking"):
        raise HTTPException(status_code=400, detail="purpose must be register/login/booking")
    if not username:
        raise HTTPException(status_code=400, detail="username required")

    req_id = str(uuid.uuid4())
    payload_str = json.dumps(inp.payload or {}, ensure_ascii=False)

    conn = db_connect()
    try:
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, code, status, payload, created_at)
            VALUES(?,?,?,?,?,?,?);
        """, (req_id, username, purpose, None, "pending", payload_str, now_utc_iso()))
        conn.commit()
    finally:
        conn.close()

    return {"request_id": req_id, "status": "pending"}

@app_api.post("/api/auth/verify")
def api_auth_verify(inp: AuthVerifyIn):
    username = normalize_username(inp.username)
    purpose = (inp.purpose or "").strip().lower()
    code = (inp.code or "").strip()

    if purpose not in ("register", "login", "booking"):
        raise HTTPException(status_code=400, detail="purpose must be register/login/booking")

    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="code must be 6 digits")

    conn = db_connect()
    try:
        row = get_latest_sent_code(conn, username, purpose)
        if not row:
            raise HTTPException(status_code=400, detail="Нет отправленного кода. Нажми 'Получить код'.")

        if row["code"] != code:
            raise HTTPException(status_code=400, detail="Неверный код")

        # mark used
        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (now_utc_iso(), row["request_id"]))
        conn.commit()

        # for booking we don't finalize here, booking finalize endpoint will check another code,
        # but to keep it simple we use purpose=booking and payload stored in request itself.
        payload = {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}

        return {"ok": True, "payload": payload}

    finally:
        conn.close()

@app_api.post("/api/passengers/register")
def api_register_passenger(p: PassengerIn):
    username = normalize_username(p.username)
    if not username:
        raise HTTPException(status_code=400, detail="username required")

    conn = db_connect()
    try:
        # create or update passenger
        conn.execute("""
            INSERT INTO passengers(passenger_id, last_name, first_name, middle_name, passport_no, phone, email)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(passenger_id) DO UPDATE SET
                last_name=excluded.last_name,
                first_name=excluded.first_name,
                middle_name=excluded.middle_name,
                passport_no=excluded.passport_no,
                phone=excluded.phone,
                email=excluded.email;
        """, (username, p.last_name, p.first_name, p.middle_name or "", p.passport_no, p.phone, p.email))
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}

@app_api.post("/api/flights/search")
def api_flights_search(inp: FlightsSearchIn):
    f = (inp.from_city or "").strip()
    t = (inp.to_city or "").strip()
    d_out = (inp.date_out or "").strip()
    d_back = (inp.date_back or "").strip()

    if not f or not t or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_out):
        raise HTTPException(status_code=400, detail="from_city/to_city/date_out required")

    conn = db_connect()
    try:
        out = conn.execute("""
            SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                   p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id = f.plane_id
            WHERE f.departure_city=? AND f.arrival_city=? AND f.flight_date=?
            ORDER BY f.flight_time;
        """, (f, t, d_out)).fetchall()

        out_list = []
        for r in out:
            # простая цена: зависит от ёмкости, чтобы не был всегда 200
            base = 120 if r["seat_capacity"] == 60 else 160 if r["seat_capacity"] == 120 else 200
            price = base + random.choice([0, 10, 20, 30, 40])
            out_list.append({
                "flight_id": r["flight_id"],
                "flight_number": r["flight_number"],
                "from": r["departure_city"],
                "to": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "seat_capacity": r["seat_capacity"],
                "price_usd": price
            })

        back_list = []
        if d_back and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_back):
            back = conn.execute("""
                SELECT f.flight_id, f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time,
                       p.seat_capacity
                FROM flights f
                JOIN planes p ON p.plane_id = f.plane_id
                WHERE f.departure_city=? AND f.arrival_city=? AND f.flight_date=?
                ORDER BY f.flight_time;
            """, (t, f, d_back)).fetchall()

            for r in back:
                base = 120 if r["seat_capacity"] == 60 else 160 if r["seat_capacity"] == 120 else 200
                price = base + random.choice([0, 10, 20, 30, 40])
                back_list.append({
                    "flight_id": r["flight_id"],
                    "flight_number": r["flight_number"],
                    "from": r["departure_city"],
                    "to": r["arrival_city"],
                    "date": r["flight_date"],
                    "time": r["flight_time"],
                    "seat_capacity": r["seat_capacity"],
                    "price_usd": price
                })

        return {"outbound": out_list, "return": back_list}
    finally:
        conn.close()

@app_api.get("/api/flights/{flight_id}/seats")
def api_flight_seats(flight_id: int):
    conn = db_connect()
    try:
        f = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="flight not found")

        seats = seat_list_for_capacity(int(f["seat_capacity"]))

        rows = conn.execute("""
            SELECT t.seat_no, s.status_name
            FROM tickets t
            JOIN ticket_statuses s ON s.status_id=t.status_id
            WHERE t.flight_id=?;
        """, (flight_id,)).fetchall()

        m = {s: "free" for s in seats}
        for r in rows:
            st = (r["status_name"] or "").lower()
            if st == "reserved":
                m[r["seat_no"]] = "reserved"
            elif st == "purchased":
                m[r["seat_no"]] = "purchased"
            else:
                m[r["seat_no"]] = "reserved"

        return {"seat_capacity": int(f["seat_capacity"]), "map": m}
    finally:
        conn.close()

@app_api.post("/api/booking/start", response_model=AuthStartOut)
def api_booking_start(inp: BookingStartIn):
    username = normalize_username(inp.username)
    seat_no = (inp.seat_no or "").strip().upper()
    if not re.fullmatch(r"[A-F]\d{1,2}", seat_no):
        raise HTTPException(status_code=400, detail="seat_no like A1..F30")

    conn = db_connect()
    try:
        ensure_passenger_exists(conn, username)

        # check seat exists for this plane
        f = conn.execute("""
            SELECT p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id=f.plane_id
            WHERE f.flight_id=?;
        """, (inp.flight_id,)).fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="flight not found")

        seats = set(seat_list_for_capacity(int(f["seat_capacity"])))
        if seat_no not in seats:
            raise HTTPException(status_code=400, detail="seat not in this plane")

        # check seat free
        busy = conn.execute("""
            SELECT 1 FROM tickets WHERE flight_id=? AND seat_no=? LIMIT 1;
        """, (inp.flight_id, seat_no)).fetchone()
        if busy:
            raise HTTPException(status_code=409, detail="seat already taken")

        # create code request purpose=booking with payload about booking
        req_id = str(uuid.uuid4())
        payload = {
            "flight_id": inp.flight_id,
            "seat_no": seat_no,
            "price_usd": float(inp.price_usd),
            "username": username
        }
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, code, status, payload, created_at)
            VALUES(?,?,?,?,?,?,?);
        """, (req_id, username, "booking", None, "pending", json.dumps(payload, ensure_ascii=False), now_utc_iso()))
        conn.commit()
        return {"request_id": req_id, "status": "pending"}
    finally:
        conn.close()

@app_api.post("/api/booking/confirm")
def api_booking_confirm(inp: AuthVerifyIn):
    # same structure: username + code (purpose must be booking)
    username = normalize_username(inp.username)
    if (inp.purpose or "").strip().lower() != "booking":
        raise HTTPException(status_code=400, detail="purpose must be booking")

    conn = db_connect()
    try:
        ensure_passenger_exists(conn, username)

        row = get_latest_sent_code(conn, username, "booking")
        if not row:
            raise HTTPException(status_code=400, detail="Нет отправленного кода бронирования")

        if (row["code"] or "") != (inp.code or "").strip():
            raise HTTPException(status_code=400, detail="Неверный код")

        payload = {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}

        flight_id = int(payload.get("flight_id", 0))
        seat_no = (payload.get("seat_no", "") or "").strip().upper()
        price_usd = float(payload.get("price_usd", 0))

        if not flight_id or not seat_no:
            raise HTTPException(status_code=400, detail="payload corrupted")

        # final check seat still free (race)
        busy = conn.execute("SELECT 1 FROM tickets WHERE flight_id=? AND seat_no=? LIMIT 1;", (flight_id, seat_no)).fetchone()
        if busy:
            raise HTTPException(status_code=409, detail="Место уже заняли. Обнови места и выбери другое.")

        # mark request used
        conn.execute("""
            UPDATE tg_code_requests
            SET status='used', used_at=?
            WHERE request_id=?;
        """, (now_utc_iso(), row["request_id"]))
        conn.commit()

        # create ticket reserved forever
        conn.execute("""
            INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd, created_at)
            VALUES(?,?,?,?,?,?);
        """, (flight_id, username, 1, seat_no, price_usd, now_utc_iso()))
        conn.commit()

        return {"ok": True, "ticket": {"flight_id": flight_id, "seat_no": seat_no, "price_usd": price_usd}}

    finally:
        conn.close()

@app_api.get("/api/my-flights")
def api_my_flights(username: str):
    username = normalize_username(username)
    conn = db_connect()
    try:
        ensure_passenger_exists(conn, username)
        rows = conn.execute("""
            SELECT t.ticket_id, t.seat_no, t.price_usd, s.status_name,
                   f.flight_number, f.departure_city, f.arrival_city, f.flight_date, f.flight_time
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            JOIN ticket_statuses s ON s.status_id=t.status_id
            WHERE t.passenger_id=?
            ORDER BY f.flight_date, f.flight_time;
        """, (username,)).fetchall()

        res = []
        for r in rows:
            res.append({
                "ticket_id": r["ticket_id"],
                "flight_number": r["flight_number"],
                "from": r["departure_city"],
                "to": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
                "seat_no": r["seat_no"],
                "status": r["status_name"],
                "price_usd": r["price_usd"],
            })
        return res
    finally:
        conn.close()

def run_api_server() -> None:
    uvicorn.run(app_api, host=API_HOST, port=API_PORT, log_level="info")

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN пустой.\n"
            "Задай его так: set BOT_TOKEN=... (в CMD) и запускай снова."
        )

    db_init()

    print(f"[bot] DB: {DB_PATH}")
    print(f"[api] http://{API_HOST}:{API_PORT}")

    # start FastAPI in background thread
    th = threading.Thread(target=run_api_server, daemon=True)
    th.start()

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
    import asyncio
    main()
