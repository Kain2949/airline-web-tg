from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import sqlite3
import datetime
import random

MAIN_DB = "airline_lab.db"
BRIDGE_DB = "airline_bridge.db"


def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def generate_code() -> str:
    return "{:06d}".format(random.randint(0, 999999))


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_bridge_conn():
    conn = sqlite3.connect(BRIDGE_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


main_conn = get_main_conn()
bridge_conn = get_bridge_conn()


def init_main_db():
    cur = main_conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS ticket_statuses
        (
            status_id   INTEGER PRIMARY KEY,
            status_name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS planes
        (
            plane_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            model             TEXT NOT NULL,
            manufacture_year  INTEGER NOT NULL,
            seat_capacity     INTEGER NOT NULL,
            CHECK (manufacture_year BETWEEN 1903 AND 2100),
            CHECK (seat_capacity BETWEEN 1 AND 1200)
        );

        CREATE TABLE IF NOT EXISTS flights
        (
            flight_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            plane_id         INTEGER NOT NULL,
            flight_number    TEXT NOT NULL UNIQUE,
            departure_city   TEXT NOT NULL,
            arrival_city     TEXT NOT NULL,
            flight_date      TEXT NOT NULL,
            flight_time      TEXT NOT NULL,
            CHECK (departure_city <> arrival_city),
            CHECK (instr(flight_number, ' ') = 0),
            FOREIGN KEY (plane_id)
                REFERENCES planes(plane_id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS passengers
        (
            passenger_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            last_name          TEXT NOT NULL,
            first_name         TEXT NOT NULL,
            middle_name        TEXT,
            passport_no        TEXT NOT NULL UNIQUE,
            birth_date         TEXT NOT NULL,
            phone              TEXT NOT NULL,
            email              TEXT NOT NULL,
            telegram_username  TEXT,
            CHECK (instr(email, '@') > 1)
        );

        CREATE TABLE IF NOT EXISTS tickets
        (
            ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id     INTEGER NOT NULL,
            passenger_id  INTEGER NOT NULL,
            status_id     INTEGER NOT NULL DEFAULT 1,
            seat_no       TEXT NOT NULL,
            price_usd     REAL NOT NULL,
            UNIQUE (flight_id, seat_no),
            FOREIGN KEY (flight_id)
                REFERENCES flights(flight_id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,
            FOREIGN KEY (passenger_id)
                REFERENCES passengers(passenger_id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,
            FOREIGN KEY (status_id)
                REFERENCES ticket_statuses(status_id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CHECK (price_usd > 0),
            CHECK (instr(seat_no, ' ') = 0)
        );

        CREATE INDEX IF NOT EXISTS idx_flights_date       ON flights (flight_date);
        CREATE INDEX IF NOT EXISTS idx_flights_route_date ON flights (departure_city, arrival_city, flight_date);
        CREATE INDEX IF NOT EXISTS idx_flights_plane      ON flights (plane_id);

        CREATE INDEX IF NOT EXISTS idx_planes_model       ON planes (model);

        CREATE INDEX IF NOT EXISTS idx_passengers_last    ON passengers (last_name);

        CREATE INDEX IF NOT EXISTS idx_tickets_status     ON tickets (status_id);
        CREATE INDEX IF NOT EXISTS idx_tickets_pass_stat  ON tickets (passenger_id, status_id);
        CREATE INDEX IF NOT EXISTS idx_tickets_flight     ON tickets (flight_id);
        """
    )

    # справочники можно предзаполнить, а вот пассажиры/билеты изначально пустые
    cur.execute("SELECT COUNT(*) AS c FROM ticket_statuses")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO ticket_statuses(status_id, status_name) VALUES (?, ?)",
            [
                (1, "Booked"),
                (2, "Paid"),
                (3, "Cancelled"),
                (4, "Checked-in"),
            ],
        )

    cur.execute("SELECT COUNT(*) AS c FROM planes")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO planes(model, manufacture_year, seat_capacity) VALUES (?, ?, ?)",
            [
                ("Airbus A320", 2011, 300),
                ("Boeing 737-800", 2015, 300),
            ],
        )

    cur.execute("SELECT COUNT(*) AS c FROM flights")
    if cur.fetchone()["c"] == 0:
        # если хочешь совсем пустую БД — просто убери этот блок вставки рейсов
        cur.executemany(
            """
            INSERT INTO flights(plane_id, flight_number, departure_city, arrival_city, flight_date, flight_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "B2-101", "Minsk", "Warsaw", "2025-12-10", "09:30:00"),
                (1, "B2-102", "Warsaw", "Minsk", "2025-12-17", "10:00:00"),
                (2, "B2-201", "Minsk", "Berlin", "2025-12-11", "13:15:00"),
                (2, "B2-202", "Berlin", "Minsk", "2025-12-18", "15:30:00"),
            ],
        )

    main_conn.commit()


def init_bridge_db():
    cur = bridge_conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS tg_users
        (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_username TEXT NOT NULL UNIQUE,
            chat_id           INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tg_codes
        (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_username TEXT NOT NULL,
            code              TEXT NOT NULL,
            purpose           TEXT NOT NULL, -- 'registration' / 'booking'
            created_at        TEXT NOT NULL,
            is_used           INTEGER NOT NULL DEFAULT 0,
            is_sent           INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tg_notifications
        (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_username TEXT NOT NULL,
            message_type      TEXT NOT NULL, -- 'registration_success'
            payload_json      TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            is_sent           INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    bridge_conn.commit()


init_main_db()
init_bridge_db()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # потом можешь сузить под домен GitHub Pages / ngrok
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========= Pydantic модели =========

class Flight(BaseModel):
    flight_id: int
    flight_number: str
    departure_city: str
    arrival_city: str
    flight_date: str
    flight_time: str


class PassengerData(BaseModel):
    last_name: str
    first_name: str
    middle_name: Optional[str] = None
    passport_no: str
    birth_date: str   # YYYY-MM-DD
    phone: str
    email: EmailStr
    telegram_username: str


class RegisterRequest(PassengerData):
    auth_request_id: int
    code: str


class RegisterResponse(BaseModel):
    passenger_id: int
    last_name: str
    first_name: str
    middle_name: Optional[str]
    birth_date: str
    passport_masked: str


class AuthStartRequest(BaseModel):
    telegram_username: str
    purpose: str  # 'registration' or 'booking'


class AuthStartResponse(BaseModel):
    request_id: int


class BookingRequest(BaseModel):
    auth_request_id: int
    code: str
    passenger_id: int
    outbound_flight_id: int
    return_flight_id: int
    outbound_seat: str
    return_seat: str


class TicketOut(BaseModel):
    ticket_id: int
    flight_id: int
    flight_number: str
    seat_no: str
    status: str
    price_usd: float


class BookingResponse(BaseModel):
    tickets: List[TicketOut]


# ========= Вспомогательные функции по БД =========

def get_seat_capacity(flight_id: int) -> int:
    cur = main_conn.cursor()
    cur.execute(
        """
        SELECT p.seat_capacity
        FROM flights f
        JOIN planes p ON p.plane_id = f.plane_id
        WHERE f.flight_id = ?
        """,
        (flight_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Flight not found")
    return row["seat_capacity"]


def validate_seat(flight_id: int, seat: str):
    if not seat.isdigit():
        raise HTTPException(status_code=400, detail="Seat must be a number (1..N)")
    seat_num = int(seat)
    capacity = get_seat_capacity(flight_id)
    rows = 6
    seats_per_row = 50
    max_seats = rows * seats_per_row
    usable_seats = min(capacity, max_seats)
    if seat_num < 1 or seat_num > usable_seats:
        raise HTTPException(status_code=400, detail="Seat out of range")

    cur = main_conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS c FROM tickets WHERE flight_id = ? AND seat_no = ?",
        (flight_id, seat),
    )
    if cur.fetchone()["c"] > 0:
        raise HTTPException(status_code=400, detail="Seat already taken")


def mask_passport(passport_no: str) -> str:
    s = passport_no.strip()
    if len(s) <= 6:
        if len(s) <= 2:
            return "*" * len(s)
        return s[0] + "*" * (len(s) - 2) + s[-1]
    return s[:3] + "*" * (len(s) - 6) + s[-3:]


def verify_code(auth_request_id: int, code: str, expected_purpose: str, telegram_username: Optional[str] = None):
    cur = bridge_conn.cursor()
    cur.execute(
        "SELECT * FROM tg_codes WHERE id = ?",
        (auth_request_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Unknown auth request")
    if row["is_used"]:
        raise HTTPException(status_code=400, detail="Code already used")
    if row["purpose"] != expected_purpose:
        raise HTTPException(status_code=400, detail="Wrong purpose for this code")
    if row["code"] != code:
        raise HTTPException(status_code=400, detail="Invalid code")
    if telegram_username and row["telegram_username"] != telegram_username:
        raise HTTPException(status_code=400, detail="Telegram username mismatch")

    bridge_conn.execute(
        "UPDATE tg_codes SET is_used = 1 WHERE id = ?",
        (auth_request_id,),
    )
    bridge_conn.commit()
    return row


# ========= API: Telegram-коды =========

@app.post("/api/auth/start", response_model=AuthStartResponse)
def start_auth(req: AuthStartRequest):
    purpose = req.purpose
    if purpose not in ("registration", "booking"):
        raise HTTPException(status_code=400, detail="Invalid purpose")

    username = req.telegram_username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Empty telegram username")

    code = generate_code()
    cur = bridge_conn.cursor()
    cur.execute(
        """
        INSERT INTO tg_codes(telegram_username, code, purpose, created_at, is_used, is_sent)
        VALUES (?, ?, ?, ?, 0, 0)
        """,
        (username, code, purpose, now_iso()),
    )
    code_id = cur.lastrowid
    bridge_conn.commit()
    return AuthStartResponse(request_id=code_id)


# ========= API: регистрация пассажира =========

@app.post("/api/register", response_model=RegisterResponse)
def register_passenger(req: RegisterRequest):
    # проверяем код для регистрации
    verify_code(req.auth_request_id, req.code, expected_purpose="registration", telegram_username=req.telegram_username)

    cur = main_conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO passengers(last_name, first_name, middle_name, passport_no,
                                   birth_date, phone, email, telegram_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.last_name.strip(),
                req.first_name.strip(),
                (req.middle_name or "").strip() or None,
                req.passport_no.strip(),
                req.birth_date.strip(),
                req.phone.strip(),
                req.email.strip(),
                req.telegram_username.strip(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Passenger with this passport already exists")

    passenger_id = cur.lastrowid
    main_conn.commit()

    # кладём уведомление для бота
    import json

    payload = {
        "passenger_id": passenger_id,
        "last_name": req.last_name.strip(),
        "first_name": req.first_name.strip(),
        "middle_name": (req.middle_name or "").strip() or "",
        "birth_date": req.birth_date.strip(),
        "passport_no": req.passport_no.strip(),
    }

    bridge_conn.execute(
        """
        INSERT INTO tg_notifications(telegram_username, message_type, payload_json, created_at, is_sent)
        VALUES (?, 'registration_success', ?, ?, 0)
        """,
        (req.telegram_username.strip(), json.dumps(payload), now_iso()),
    )
    bridge_conn.commit()

    return RegisterResponse(
        passenger_id=passenger_id,
        last_name=req.last_name.strip(),
        first_name=req.first_name.strip(),
        middle_name=(req.middle_name or "").strip() or None,
        birth_date=req.birth_date.strip(),
        passport_masked=mask_passport(req.passport_no),
    )


# ========= API: рейсы и места =========

@app.get("/api/flights", response_model=List[Flight])
def search_flights(
    departure_city: str,
    arrival_city: str,
    date: str,
):
    cur = main_conn.cursor()
    cur.execute(
        """
        SELECT flight_id, flight_number, departure_city, arrival_city, flight_date, flight_time
        FROM flights
        WHERE departure_city = ?
          AND arrival_city = ?
          AND flight_date = ?
        ORDER BY flight_time
        """,
        (departure_city, arrival_city, date),
    )
    rows = cur.fetchall()
    return [dict(row) for row in rows]


@app.get("/api/flights/{flight_id}/seats")
def get_seats(flight_id: int):
    capacity = get_seat_capacity(flight_id)
    rows = 6
    seats_per_row = 50
    max_seats = rows * seats_per_row
    usable_seats = min(capacity, max_seats)

    cur = main_conn.cursor()
    cur.execute(
        "SELECT seat_no FROM tickets WHERE flight_id = ?",
        (flight_id,),
    )
    taken = [r["seat_no"] for r in cur.fetchall()]

    return {
        "rows": rows,
        "seats_per_row": seats_per_row,
        "usable_seats": usable_seats,
        "taken": taken,
    }


# ========= API: бронирование с кодом =========

@app.post("/api/book", response_model=BookingResponse)
def book_trip(req: BookingRequest):
    # проверяем код для бронирования
    verify_code(req.auth_request_id, req.code, expected_purpose="booking")

    cur = main_conn.cursor()

    # проверяем, что пассажир существует
    cur.execute(
        "SELECT * FROM passengers WHERE passenger_id = ?",
        (req.passenger_id,),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=400, detail="Passenger not found")

    # проверяем и блокируем места
    validate_seat(req.outbound_flight_id, req.outbound_seat)
    validate_seat(req.return_flight_id, req.return_seat)

    price_out = 120.0
    price_ret = 130.0

    def insert_ticket(flight_id: int, seat: str, price: float) -> int:
        cur.execute(
            """
            INSERT INTO tickets(flight_id, passenger_id, status_id, seat_no, price_usd)
            VALUES (?, ?, 1, ?, ?)
            """,
            (flight_id, req.passenger_id, seat, price),
        )
        return cur.lastrowid

    out_ticket_id = insert_ticket(req.outbound_flight_id, req.outbound_seat, price_out)
    ret_ticket_id = insert_ticket(req.return_flight_id, req.return_seat, price_ret)

    cur.execute(
        """
        SELECT t.ticket_id, t.flight_id, f.flight_number, t.seat_no, s.status_name, t.price_usd
        FROM tickets t
        JOIN flights f ON f.flight_id = t.flight_id
        JOIN ticket_statuses s ON s.status_id = t.status_id
        WHERE t.ticket_id IN (?, ?)
        """,
        (out_ticket_id, ret_ticket_id),
    )
    tickets_out = []
    for r in cur.fetchall():
        tickets_out.append(
            TicketOut(
                ticket_id=r["ticket_id"],
                flight_id=r["flight_id"],
                flight_number=r["flight_number"],
                seat_no=r["seat_no"],
                status=r["status_name"],
                price_usd=r["price_usd"],
            )
        )

    main_conn.commit()

    return BookingResponse(tickets=tickets_out)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
