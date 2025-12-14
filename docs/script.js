const I18N = {
  ru: {
    title: "Авиакомпания: регистрация и бронирование",
    step1: "1. Подтверждение через Telegram",
    getCode: "Получить код",
    step2: "2. Регистрация пассажира",
    finishReg: "Завершить регистрацию",
    step3: "3. Бронирование рейса",
    findFlights: "Найти рейсы",
    getBookCode: "Получить код",
    book: "Забронировать",
  },
  en: {
    title: "Airline: registration & booking",
    step1: "1. Telegram verification",
    getCode: "Get code",
    step2: "2. Passenger registration",
    finishReg: "Finish registration",
    step3: "3. Flight booking",
    findFlights: "Search flights",
    getBookCode: "Get code",
    book: "Book",
  }
};

let lang = localStorage.getItem("lang") || "ru";

function applyLang() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (I18N[lang] && I18N[lang][key]) el.textContent = I18N[lang][key];
  });

  // кнопка показывает, на какой язык переключит
  const btn = document.getElementById("lang-toggle");
  if (btn) btn.textContent = (lang === "ru") ? "EN" : "RU";

  localStorage.setItem("lang", lang);
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("lang-toggle");
  if (btn) {
    btn.addEventListener("click", () => {
      lang = (lang === "ru") ? "en" : "ru";
      applyLang();
    });
  }
  applyLang();
});


// тут меняешь на URL сервера (ngrok и т.д.)
const API_BASE = "https://kristan-labored-earsplittingly.ngrok-free.dev";

let tgAuthRequestId = null;      // для регистрации
let bookingAuthRequestId = null; // для бронирования
let currentTgUsername = null;

const tgForm = document.getElementById("tg-form");
const tgCodeBlock = document.getElementById("tg-code-block");
const regAuthInput = document.getElementById("reg-auth-request-id");
const regForm = document.getElementById("register-form");
const regResult = document.getElementById("reg-result");
const bookingSection = document.getElementById("booking-section");

const searchForm = document.getElementById("search-form");
const outboundFlightsDiv = document.getElementById("outbound-flights");
const returnFlightsDiv = document.getElementById("return-flights");
const outboundSeatsDiv = document.getElementById("outbound-seats");
const returnSeatsDiv = document.getElementById("return-seats");

const passengerIdInput = document.getElementById("passenger-id");
const outboundFlightIdInput = document.getElementById("outbound-flight-id");
const returnFlightIdInput = document.getElementById("return-flight-id");
const outboundSeatInput = document.getElementById("outbound-seat");
const returnSeatInput = document.getElementById("return-seat");

const bookForm = document.getElementById("booking-form");
const bookCodeInput = document.getElementById("book-code");
const bookGetCodeBtn = document.getElementById("book-get-code");
const bookAuthInput = document.getElementById("book-auth-request-id");
const bookingResult = document.getElementById("booking-result");


// ===== Telegram авторизация (старт кода для регистрации) =====

tgForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const usernameRaw = document.getElementById("tg-username").value.trim();
    if (!usernameRaw) {
        alert("Введи Telegram username");
        return;
    }
    currentTgUsername = usernameRaw;

    try {
        const res = await fetch(`${API_BASE}/api/auth/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                telegram_username: currentTgUsername,
                purpose: "registration",
            }),
        });

        const data = await res.json();
        if (!res.ok) {
            alert("Ошибка: " + (data.detail || res.status));
            return;
        }

        tgAuthRequestId = data.request_id;
        regAuthInput.value = tgAuthRequestId;
        tgCodeBlock.classList.remove("hidden");
    } catch (err) {
        console.error(err);
        alert("Не удалось достучаться до сервера (регистрация).");
    }
});


// ===== Регистрация пассажира =====

regForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!tgAuthRequestId || !currentTgUsername) {
        alert("Сначала запроси код через Telegram в шаге 1.");
        return;
    }

    const payload = {
        auth_request_id: tgAuthRequestId,
        code: document.getElementById("reg-code").value.trim(),
        last_name: document.getElementById("last-name").value.trim(),
        first_name: document.getElementById("first-name").value.trim(),
        middle_name: document.getElementById("middle-name").value.trim() || null,
        passport_no: document.getElementById("passport-no").value.trim(),
        birth_date: document.getElementById("birth-date").value,
        phone: document.getElementById("phone").value.trim(),
        email: document.getElementById("email").value.trim(),
        telegram_username: currentTgUsername,
    };

    regResult.textContent = "";

    try {
        const res = await fetch(`${API_BASE}/api/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();

        if (!res.ok) {
            regResult.textContent = "Ошибка регистрации: " + (data.detail || res.status);
            return;
        }

        regResult.textContent =
            `Регистрация успешна.\n` +
            `ID пассажира: ${data.passenger_id}\n` +
            `Паспорт (маска): ${data.passport_masked}`;

        passengerIdInput.value = data.passenger_id;

        // открываем блок бронирования
        bookingSection.classList.remove("hidden");
    } catch (err) {
        console.error(err);
        regResult.textContent = "Ошибка сети при регистрации.";
    }
});


// ===== Поиск рейсов =====

searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const from = document.getElementById("from").value;
    const to = document.getElementById("to").value;
    const dateOut = document.getElementById("date-outbound").value;
    const dateBack = document.getElementById("date-return").value;

    if (!from || !to || !dateOut || !dateBack) {
        alert("Заполни направления и даты.");
        return;
    }

    try {
        const [outRes, retRes] = await Promise.all([
            fetch(
                `${API_BASE}/api/flights?departure_city=${encodeURIComponent(
                    from
                )}&arrival_city=${encodeURIComponent(
                    to
                )}&date=${encodeURIComponent(dateOut)}`
            ),
            fetch(
                `${API_BASE}/api/flights?departure_city=${encodeURIComponent(
                    to
                )}&arrival_city=${encodeURIComponent(
                    from
                )}&date=${encodeURIComponent(dateBack)}`
            ),
        ]);

        const outFlights = await outRes.json();
        const retFlights = await retRes.json();

        renderFlights("outbound-flights", outFlights, "outbound");
        renderFlights("return-flights", retFlights, "return");
    } catch (err) {
        console.error(err);
        alert("Ошибка загрузки рейсов.");
    }
});

function renderFlights(containerId, flights, type) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";

    if (!flights || flights.length === 0) {
        container.textContent = "Нет подходящих рейсов";
        return;
    }

    flights.forEach((f) => {
        const label = document.createElement("label");
        label.className = "flight-option";

        const input = document.createElement("input");
        input.type = "radio";
        input.name = type === "outbound" ? "outboundFlight" : "returnFlight";
        input.value = f.flight_id;

        input.addEventListener("change", () => {
            const hiddenId =
                type === "outbound"
                    ? outboundFlightIdInput
                    : returnFlightIdInput;
            hiddenId.value = f.flight_id;
            loadSeats(f.flight_id, type);
        });

        const span = document.createElement("span");
        span.textContent = `${f.flight_number} ${f.departure_city} → ${f.arrival_city} ${f.flight_date} ${f.flight_time}`;

        label.appendChild(input);
        label.appendChild(span);
        container.appendChild(label);
    });
}


// ===== Загрузка и выбор мест =====

async function loadSeats(flightId, type) {
    const container =
        type === "outbound" ? outboundSeatsDiv : returnSeatsDiv;

    container.innerHTML = "Загрузка мест...";

    try {
        const res = await fetch(`${API_BASE}/api/flights/${flightId}/seats`);
        const data = await res.json();
        renderSeats(container, data, type);
    } catch (err) {
        console.error(err);
        container.textContent = "Ошибка загрузки мест";
    }
}

function renderSeats(container, seatData, type) {
    container.innerHTML = "";

    const rows = seatData.rows;
    const seatsPerRow = seatData.seats_per_row;
    const usable = seatData.usable_seats;
    const takenSet = new Set(seatData.taken || []);

    const selectedSeatInput =
        type === "outbound" ? outboundSeatInput : returnSeatInput;
    selectedSeatInput.value = "";

    let seatNumber = 1;

    for (let r = 1; r <= rows; r++) {
        const rowDiv = document.createElement("div");
        rowDiv.className = "seat-row";

        for (let s = 1; s <= seatsPerRow; s++) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "seat";
            const seatStr = String(seatNumber);
            btn.textContent = seatStr;

            const isOutOfRange = seatNumber > usable;
            const isTaken = takenSet.has(seatStr);

            if (isOutOfRange || isTaken) {
                btn.disabled = true;
                btn.classList.add("seat-disabled");
            } else {
                btn.addEventListener("click", () => {
                    const previously =
                        container.querySelectorAll(".seat-selected");
                    previously.forEach((b) =>
                        b.classList.remove("seat-selected")
                    );
                    btn.classList.add("seat-selected");
                    selectedSeatInput.value = seatStr;
                });
            }

            rowDiv.appendChild(btn);
            seatNumber++;
        }

        container.appendChild(rowDiv);
    }
}


// ===== Коды для бронирования =====

bookGetCodeBtn.addEventListener("click", async () => {
    if (!currentTgUsername) {
        alert("Сначала подтверди Telegram в шаге 1 и зарегистрируйся.");
        return;
    }

    if (
        !passengerIdInput.value ||
        !outboundFlightIdInput.value ||
        !returnFlightIdInput.value ||
        !outboundSeatInput.value ||
        !returnSeatInput.value
    ) {
        alert("Выбери рейсы и места, прежде чем запрашивать код.");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/auth/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                telegram_username: currentTgUsername,
                purpose: "booking",
            }),
        });

        const data = await res.json();
        if (!res.ok) {
            alert("Ошибка: " + (data.detail || res.status));
            return;
        }

        bookingAuthRequestId = data.request_id;
        bookAuthInput.value = bookingAuthRequestId;
        alert("Код для бронирования отправлен в Telegram бота.");
    } catch (err) {
        console.error(err);
        alert("Ошибка при запросе кода бронирования.");
    }
});


// ===== Бронирование =====

bookForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!bookingAuthRequestId) {
        alert("Сначала нажми 'Получить код', чтобы бот отправил код подтверждения.");
        return;
    }

    const payload = {
        auth_request_id: bookingAuthRequestId,
        code: bookCodeInput.value.trim(),
        passenger_id: Number(passengerIdInput.value),
        outbound_flight_id: Number(outboundFlightIdInput.value),
        return_flight_id: Number(returnFlightIdInput.value),
        outbound_seat: outboundSeatInput.value,
        return_seat: returnSeatInput.value,
    };

    bookingResult.textContent = "";

    try {
        const res = await fetch(`${API_BASE}/api/book`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();

        if (!res.ok) {
            bookingResult.textContent =
                "Ошибка бронирования: " + (data.detail || res.status);
            return;
        }

        bookingResult.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
        console.error(err);
        bookingResult.textContent = "Ошибка сети при бронировании.";
    }
});
