# bot/botinok.py
# Telegram-бот + FastAPI backend в одном файле.
# Коды: только после выбора (register/login/booking).
# passenger_id = Telegram @username
# Места реально бронируются в SQLite и видны всем.

import os
import re
import json
import uuid
import math
import random
import sqlite3
import threading
import asyncio
import zlib
from pathlib import Path
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "airline_lab.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "1488"))

POLL_SECONDS = float(os.getenv("BOT_POLL_SECONDS", "2.0"))
REQUIRE_USERNAME = True  # требовать @username

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

def mask_passport(passport: str) -> str:
    p = (passport or "").strip()
    if not p:
        return ""
    if len(p) <= 6:
        if len(p) <= 2:
            return "*" * len(p)
        return p[0] + "*" * (len(p) - 2) + p[-1]
    return p[:3] + "*" * (len(p) - 6) + p[-3:]

def seat_row_labels(n_rows: int) -> list[str]:
    labels = []
    for i in range(n_rows):
        x = i
        s = ""
        while True:
            s = chr(ord("A") + (x % 26)) + s
            x = x // 26 - 1
            if x < 0:
                break
        labels.append(s)
    return labels

def seat_labels(capacity: int) -> list[str]:
    cols = 6
    rows = int(math.ceil(capacity / cols))
    labels = []
    rlabels = seat_row_labels(rows)
    for r in rlabels:
        for c in range(1, cols + 1):
            labels.append(f"{r}{c}")
    return labels[:capacity]

def compute_price_usd(flight_number: str, flight_date: str, flight_time: str) -> int:
    s = f"{flight_number}|{flight_date}|{flight_time}"
    h = zlib.crc32(s.encode("utf-8")) & 0xffffffff
    return 120 + (h % 181)  # 120..300

# =========================
# DB
# =========================

def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == col for r in rows)

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table,)
    ).fetchone()
    return bool(r)

def is_legacy_passengers(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "passengers"):
        return False
    info = conn.execute("PRAGMA table_info(passengers);").fetchall()
    for r in info:
        if r["name"] == "passenger_id":
            t = (r["type"] or "").upper()
            return (r["pk"] == 1) and ("INT" in t)
    return False

def migrate_passengers_to_text(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF;")
    if table_exists(conn, "tickets"):
        conn.execute("ALTER TABLE tickets RENAME TO tickets_old;")
    conn.execute("ALTER TABLE passengers RENAME TO passengers_old;")
    conn.commit()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS passengers (
        passenger_id TEXT PRIMARY KEY,
        last_name    TEXT NOT NULL,
        first_name   TEXT NOT NULL,
        middle_name  TEXT,
        passport_no  TEXT NOT NULL,
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
        passenger_id  TEXT NOT NULL,
        status_id     INTEGER NOT NULL,
        seat_no       TEXT NOT NULL,
        price_usd     REAL NOT NULL,
        FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
        FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
        FOREIGN KEY (status_id) REFERENCES ticket_statuses(status_id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_flight ON tickets(flight_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_pass_stat ON tickets(passenger_id, status_id);")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_flight_seat ON tickets(flight_id, seat_no);")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON;")

def db_init() -> None:
    conn = db_connect()
    try:
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
            flight_date      TEXT NOT NULL,
            flight_time      TEXT NOT NULL,
            FOREIGN KEY (plane_id) REFERENCES planes(plane_id)
        );
        """)

        if is_legacy_passengers(conn):
            migrate_passengers_to_text(conn)
        else:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS passengers (
                passenger_id TEXT PRIMARY KEY,
                last_name    TEXT NOT NULL,
                first_name   TEXT NOT NULL,
                middle_name  TEXT,
                passport_no  TEXT NOT NULL,
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
                passenger_id  TEXT NOT NULL,
                status_id     INTEGER NOT NULL,
                seat_no       TEXT NOT NULL,
                price_usd     REAL NOT NULL,
                FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
                FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
                FOREIGN KEY (status_id) REFERENCES ticket_statuses(status_id)
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_flight ON tickets(flight_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_pass_stat ON tickets(passenger_id, status_id);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_flight_seat ON tickets(flight_id, seat_no);")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_flights_date ON flights(flight_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flights_route_date ON flights(departure_city, arrival_city, flight_date);")

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
            purpose     TEXT,
            kind        TEXT,
            code        TEXT,
            status      TEXT NOT NULL,
            payload     TEXT,
            created_at  TEXT NOT NULL,
            sent_at     TEXT,
            used_at     TEXT
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_code_pending ON tg_code_requests(status, created_at);")

        if not col_exists(conn, "tg_code_requests", "payload"):
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN payload TEXT;")
        if not col_exists(conn, "tg_code_requests", "purpose"):
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN purpose TEXT;")
        if not col_exists(conn, "tg_code_requests", "kind"):
            conn.execute("ALTER TABLE tg_code_requests ADD COLUMN kind TEXT;")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_notifications (
            notif_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            kind        TEXT NOT NULL,
            message     TEXT,
            payload     TEXT,
            status      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            sent_at     TEXT
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_notif_pending ON tg_notifications(status, created_at);")

        for s in ["booked", "sold"]:
            conn.execute("INSERT OR IGNORE INTO ticket_statuses(status_name) VALUES (?);", (s,))

        planes = [
            ("Boeing 737-800", 2012, 60),
            ("Airbus A320", 2016, 120),
            ("Boeing 777-300ER", 2014, 180),
        ]
        for model, year, cap in planes:
            conn.execute("""
                INSERT OR IGNORE INTO planes(model, manufacture_year, seat_capacity)
                VALUES (?, ?, ?);
            """, (model, year, cap))

        conn.commit()
    finally:
        conn.close()

def status_id_by_name(conn: sqlite3.Connection, name: str) -> int:
    r = conn.execute("SELECT status_id FROM ticket_statuses WHERE status_name=?;", (name,)).fetchone()
    if not r:
        raise RuntimeError("ticket_statuses not seeded")
    return int(r["status_id"])

def ensure_flights(conn: sqlite3.Connection, dep: str, arr: str, date: str) -> list[sqlite3.Row]:
    rows = conn.execute("""
        SELECT f.*, p.seat_capacity
        FROM flights f
        JOIN planes p ON p.plane_id = f.plane_id
        WHERE f.departure_city=? AND f.arrival_city=? AND f.flight_date=?
        ORDER BY f.flight_time;
    """, (dep, arr, date)).fetchall()
    if rows:
        return rows

    times = ["07:10", "09:40", "12:15", "15:30", "18:05", "21:20"]
    plane_ids = [r["plane_id"] for r in conn.execute("SELECT plane_id FROM planes;").fetchall()]
    if not plane_ids:
        raise RuntimeError("planes not seeded")

    for t in times:
        plane_id = random.choice(plane_ids)
        fn = f"BY{random.randint(100, 999)}{random.randint(0, 9)}"
        conn.execute("""
            INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (plane_id, fn, dep, arr, date, t))
    conn.commit()

    rows = conn.execute("""
        SELECT f.*, p.seat_capacity
        FROM flights f
        JOIN planes p ON p.plane_id = f.plane_id
        WHERE f.departure_city=? AND f.arrival_city=? AND f.flight_date=?
        ORDER BY f.flight_time;
    """, (dep, arr, date)).fetchall()
    return rows

# =========================
# FASTAPI MODELS
# =========================

class AuthRequestCodeIn(BaseModel):
    username: str = Field(..., min_length=1)
    purpose: str = Field(..., pattern="^(register|login)$")

class AuthConfirmRegisterIn(BaseModel):
    username: str
    code: str = Field(..., pattern=r"^\d{6}$")
    last_name: str
    first_name: str
    middle_name: str | None = ""
    passport_no: str
    phone: str
    email: str

class AuthConfirmLoginIn(BaseModel):
    username: str
    code: str = Field(..., pattern=r"^\d{6}$")

class BookingRequestCodeIn(BaseModel):
    username: str
    selections: list[dict]

class BookingConfirmIn(BaseModel):
    username: str
    code: str = Field(..., pattern=r"^\d{6}$")

# =========================
# FASTAPI APP
# =========================

api = FastAPI(title="Airline Web TG API", version="1.0")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@api.get("/api/health")
def api_health():
    return {"ok": True, "db": str(DB_PATH)}

def require_started_in_bot(conn: sqlite3.Connection, username: str) -> None:
    r = conn.execute("SELECT chat_id FROM tg_users WHERE username=?;", (username,)).fetchone()
    if not r:
        raise HTTPException(
            status_code=400,
            detail="Сначала открой бота и нажми /start. Иначе он не сможет прислать код."
        )

def find_latest_sent_code(conn: sqlite3.Connection, username: str, purpose: str) -> sqlite3.Row | None:
    return conn.execute("""
        SELECT request_id, code, payload, status, created_at, sent_at
        FROM tg_code_requests
        WHERE username=?
          AND status='sent'
          AND (purpose=? OR kind=?)
        ORDER BY created_at DESC
        LIMIT 1;
    """, (username, purpose, purpose)).fetchone()

def mark_code_used(conn: sqlite3.Connection, request_id: str) -> None:
    conn.execute("""
        UPDATE tg_code_requests
        SET status='used', used_at=?
        WHERE request_id=?;
    """, (now_utc_iso(), request_id))

@api.post("/api/auth/request-code")
def api_auth_request_code(body: AuthRequestCodeIn):
    username = normalize_username(body.username)
    purpose = body.purpose.strip()

    if REQUIRE_USERNAME and not username:
        raise HTTPException(status_code=400, detail="Нужен Telegram @username.")

    conn = db_connect()
    try:
        require_started_in_bot(conn, username)
        rid = uuid.uuid4().hex
        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, kind, status, payload, created_at)
            VALUES (?, ?, ?, ?, 'pending', NULL, ?);
        """, (rid, username, purpose, purpose, ts))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@api.post("/api/auth/confirm-register")
def api_auth_confirm_register(body: AuthConfirmRegisterIn):
    username = normalize_username(body.username)
    code = body.code.strip()

    conn = db_connect()
    try:
        row = find_latest_sent_code(conn, username, "register")
        if not row or (row["code"] or "") != code:
            raise HTTPException(status_code=400, detail="Код неверный или устарел.")

        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO passengers(passenger_id, last_name, first_name, middle_name, passport_no, phone, email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(passenger_id) DO UPDATE SET
              last_name=excluded.last_name,
              first_name=excluded.first_name,
              middle_name=excluded.middle_name,
              passport_no=excluded.passport_no,
              phone=excluded.phone,
              email=excluded.email,
              updated_at=excluded.updated_at;
        """, (
            username,
            body.last_name.strip(),
            body.first_name.strip(),
            (body.middle_name or "").strip(),
            body.passport_no.strip(),
            body.phone.strip(),
            body.email.strip(),
            ts, ts
        ))
        mark_code_used(conn, str(row["request_id"]))

        payload = json.dumps({
            "fio": f"{body.last_name.strip()} {body.first_name.strip()} {(body.middle_name or '').strip()}".strip(),
            "passport_no": body.passport_no.strip(),
        }, ensure_ascii=False)

        conn.execute("""
            INSERT INTO tg_notifications(username, kind, message, payload, status, created_at)
            VALUES (?, 'registration_success', NULL, ?, 'pending', ?);
        """, (username, payload, ts))

        conn.commit()
        return {"ok": True, "username": username}
    finally:
        conn.close()

@api.post("/api/auth/confirm-login")
def api_auth_confirm_login(body: AuthConfirmLoginIn):
    username = normalize_username(body.username)
    code = body.code.strip()

    conn = db_connect()
    try:
        row = find_latest_sent_code(conn, username, "login")
        if not row or (row["code"] or "") != code:
            raise HTTPException(status_code=400, detail="Код неверный или устарел.")

        p = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Пользователь не зарегистрирован. Нужна регистрация.")

        mark_code_used(conn, str(row["request_id"]))
        conn.commit()
        return {"ok": True, "username": username}
    finally:
        conn.close()

@api.get("/api/flights/search")
def api_flights_search(
    from_city: str,
    from_country: str = "",
    to_city: str = "",
    to_country: str = "",
    date: str = ""
):
    def pack(city: str, country: str) -> str:
        city = (city or "").strip()
        country = (country or "").strip()
        if country:
            return f"{city} ({country})"
        return city

    dep = pack(from_city, from_country)
    arr = pack(to_city, to_country)
    date = (date or "").strip()

    if not dep or not arr or not date:
        raise HTTPException(status_code=400, detail="Заполни город отправления, город прибытия и дату.")

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(status_code=400, detail="Дата должна быть в формате YYYY-MM-DD.")

    conn = db_connect()
    try:
        rows = ensure_flights(conn, dep, arr, date)
        out = []
        for r in rows:
            price = compute_price_usd(r["flight_number"], r["flight_date"], r["flight_time"])
            out.append({
                "flight_id": int(r["flight_id"]),
                "flight_number": r["flight_number"],
                "departure_city": r["departure_city"],
                "arrival_city": r["arrival_city"],
                "flight_date": r["flight_date"],
                "flight_time": r["flight_time"],
                "seat_capacity": int(r["seat_capacity"]),
                "price_usd": price,
            })
        return {"ok": True, "flights": out}
    finally:
        conn.close()

@api.get("/api/flights/{flight_id}/seats")
def api_flight_seats(flight_id: int):
    conn = db_connect()
    try:
        f = conn.execute("""
            SELECT f.flight_id, p.seat_capacity
            FROM flights f
            JOIN planes p ON p.plane_id = f.plane_id
            WHERE f.flight_id=?;
        """, (flight_id,)).fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="Рейс не найден.")

        cap = int(f["seat_capacity"])
        all_seats = seat_labels(cap)

        taken = conn.execute("""
            SELECT t.seat_no, s.status_name
            FROM tickets t
            JOIN ticket_statuses s ON s.status_id=t.status_id
            WHERE t.flight_id=?;
        """, (flight_id,)).fetchall()

        taken_map = {r["seat_no"]: r["status_name"] for r in taken}

        seats = []
        for s in all_seats:
            st = taken_map.get(s, "free")
            seats.append({"seat_no": s, "status": st})

        return {"ok": True, "capacity": cap, "seats": seats}
    finally:
        conn.close()

@api.post("/api/booking/request-code")
def api_booking_request_code(body: BookingRequestCodeIn):
    username = normalize_username(body.username)
    selections = body.selections or []

    if not selections:
        raise HTTPException(status_code=400, detail="Нет выбранных рейсов/мест.")

    conn = db_connect()
    try:
        require_started_in_bot(conn, username)

        p = conn.execute("SELECT passenger_id FROM passengers WHERE passenger_id=?;", (username,)).fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Сначала зарегистрируйся.")

        for sel in selections:
            fid = int(sel.get("flight_id", 0))
            seat_no = str(sel.get("seat_no", "")).strip().upper()
            if not fid or not seat_no:
                raise HTTPException(status_code=400, detail="Кривой выбор рейса/места.")
            exists = conn.execute("""
                SELECT 1 FROM tickets
                WHERE flight_id=? AND seat_no=?;
            """, (fid, seat_no)).fetchone()
            if exists:
                raise HTTPException(status_code=409, detail=f"Место {seat_no} уже занято.")

        rid = uuid.uuid4().hex
        ts = now_utc_iso()
        payload = json.dumps({"selections": selections}, ensure_ascii=False)

        conn.execute("""
            INSERT INTO tg_code_requests(request_id, username, purpose, kind, status, payload, created_at)
            VALUES (?, ?, 'booking', 'booking', 'pending', ?, ?);
        """, (rid, username, payload, ts))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@api.post("/api/booking/confirm")
def api_booking_confirm(body: BookingConfirmIn):
    username = normalize_username(body.username)
    code = body.code.strip()

    conn = db_connect()
    try:
        row = find_latest_sent_code(conn, username, "booking")
        if not row or (row["code"] or "") != code:
            raise HTTPException(status_code=400, detail="Код неверный или устарел.")

        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}

        selections = payload.get("selections") or []
        if not selections:
            raise HTTPException(status_code=400, detail="Пустая заявка бронирования.")

        booked_id = status_id_by_name(conn, "booked")

        for sel in selections:
            fid = int(sel.get("flight_id", 0))
            seat_no = str(sel.get("seat_no", "")).strip().upper()
            exists = conn.execute("SELECT 1 FROM tickets WHERE flight_id=? AND seat_no=?;", (fid, seat_no)).fetchone()
            if exists:
                raise HTTPException(status_code=409, detail=f"Место {seat_no} уже занято.")

        for sel in selections:
            fid = int(sel.get("flight_id"))
            seat_no = str(sel.get("seat_no")).strip().upper()
            price = float(sel.get("price_usd", 0))
            if price <= 0:
                f = conn.execute("SELECT flight_number, flight_date, flight_time FROM flights WHERE flight_id=?;", (fid,)).fetchone()
                if f:
                    price = float(compute_price_usd(f["flight_number"], f["flight_date"], f["flight_time"]))
                else:
                    price = 200.0

            conn.execute("""
                INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd)
                VALUES (?, ?, ?, ?, ?);
            """, (fid, username, booked_id, seat_no, price))

        mark_code_used(conn, str(row["request_id"]))

        ts = now_utc_iso()
        conn.execute("""
            INSERT INTO tg_notifications(username, kind, message, payload, status, created_at)
            VALUES (?, 'booking_success', NULL, ?, 'pending', ?);
        """, (username, json.dumps({"details": "Бронирование подтверждено."}, ensure_ascii=False), ts))

        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@api.get("/api/my-flights")
def api_my_flights(username: str):
    username = normalize_username(username)
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT
              t.ticket_id,
              t.seat_no,
              t.price_usd,
              s.status_name,
              f.flight_number,
              f.departure_city,
              f.arrival_city,
              f.flight_date,
              f.flight_time
            FROM tickets t
            JOIN flights f ON f.flight_id=t.flight_id
            JOIN ticket_statuses s ON s.status_id=t.status_id
            WHERE t.passenger_id=?
            ORDER BY f.flight_date, f.flight_time;
        """, (username,)).fetchall()

        out = []
        for r in rows:
            out.append({
                "ticket_id": int(r["ticket_id"]),
                "seat_no": r["seat_no"],
                "price_usd": float(r["price_usd"]),
                "status": r["status_name"],
                "flight_number": r["flight_number"],
                "from": r["departure_city"],
                "to": r["arrival_city"],
                "date": r["flight_date"],
                "time": r["flight_time"],
            })
        return {"ok": True, "tickets": out}
    finally:
        conn.close()

# =========================
# TELEGRAM BOT
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    chat = update.effective_chat

    username = normalize_username(u.username or "")
    if REQUIRE_USERNAME and not username:
        await update.message.reply_text(
            "У тебя не задан @username в Telegram.\n"
            "Настройки Telegram → Username. Потом снова /start."
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
        "Теперь возвращайся в приложение и запрашивай коды."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — привязать твой Telegram\n"
        "/help — помощь\n\n"
        "Коды подтверждения приходят сюда, когда ты их запросишь в веб-приложении."
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if re.fullmatch(r"\d{6}", txt):
        await update.message.reply_text("Код вводится в веб-приложении, не тут 😈")
        return
    await update.message.reply_text("Я тут только для кодов. /start — чтобы привязаться.")

async def process_pending_codes(app: Application) -> None:
    conn = db_connect()
    try:
        rows = conn.execute("""
            SELECT request_id, username, COALESCE(purpose, kind, '') AS purpose
            FROM tg_code_requests
            WHERE status='pending'
            ORDER BY created_at
            LIMIT 20;
        """).fetchall()

        for r in rows:
            req_id = r["request_id"]
            username = normalize_username(r["username"])
            purpose = (r["purpose"] or "").strip()

            user = conn.execute(
                "SELECT chat_id FROM tg_users WHERE username=?",
                (username,)
            ).fetchone()
            if not user:
                continue

            code = gen_code()
            title = {
                "register": "Регистрация",
                "login": "Вход",
                "booking": "Бронирование",
            }.get(purpose, purpose or "Подтверждение")

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
                try:
                    obj = json.loads(payload)
                except Exception:
                    obj = {}

                if kind == "registration_success":
                    fio = obj.get("fio") or ""
                    passport = obj.get("passport_no") or ""
                    message = (
                        "✅ Регистрация успешна.\n\n"
                        f"ФИО: <b>{fio}</b>\n"
                        f"Паспорт: <b>{mask_passport(passport)}</b>"
                    )
                elif kind == "booking_success":
                    details = obj.get("details") or "Бронирование подтверждено."
                    message = "✅ " + str(details)
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

async def job_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    await process_pending_codes(app)
    await process_pending_notifications(app)

# =========================
# RUNNERS
# =========================

def run_api_server() -> None:
    config = uvicorn.Config(api, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN пустой. Задай переменную окружения BOT_TOKEN и запускай снова.")

    db_init()
    print(f"[bot] DB: {DB_PATH}")
    print(f"[api] http://{API_HOST}:{API_PORT}")

    t = threading.Thread(target=run_api_server, daemon=True)
    t.start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.job_queue.run_repeating(job_tick, interval=POLL_SECONDS, first=1.0)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
