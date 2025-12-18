(() =>
{
  const FIXED_NGROK = "https://kristan-labored-earsplittingly.ngrok-free.dev";
  const API_BASE = (location.hostname.endsWith("github.io"))
    ? FIXED_NGROK
    : location.origin;

  const el =
  {
    langBtn: document.getElementById("langBtn"),
    toast: document.getElementById("toast"),

    tgUsername: document.getElementById("tgUsername"),
    btnGetRegCode: document.getElementById("btnGetRegCode"),

    lastName: document.getElementById("lastName"),
    firstName: document.getElementById("firstName"),
    middleName: document.getElementById("middleName"),
    passportNo: document.getElementById("passportNo"),
    birthDate: document.getElementById("birthDate"),
    phone: document.getElementById("phone"),
    email: document.getElementById("email"),
    regCode: document.getElementById("regCode"),
    btnRegister: document.getElementById("btnRegister"),
    regOk: document.getElementById("regOk"),

    bookingCard: document.getElementById("bookingCard"),
    flightSelect: document.getElementById("flightSelect"),
    flightMeta: document.getElementById("flightMeta"),
    seats: document.getElementById("seats"),
    btnReloadSeats: document.getElementById("btnReloadSeats"),
    pickedSeat: document.getElementById("pickedSeat"),
    priceUsd: document.getElementById("priceUsd"),
    btnGetBookCode: document.getElementById("btnGetBookCode"),
    bookConfirmRow: document.getElementById("bookConfirmRow"),
    bookCode: document.getElementById("bookCode"),
    btnConfirmBooking: document.getElementById("btnConfirmBooking"),

    myFlightsCard: document.getElementById("myFlightsCard"),
    btnMyFlights: document.getElementById("btnMyFlights"),
    myFlights: document.getElementById("myFlights"),
    whoami: document.getElementById("whoami")
  };

  let currentLang = "ru";
  let flights = [];
  let takenSet = new Set();
  let capacity = 0;
  let selectedSeat = "";

  const i18n =
  {
    ru:
    {
      title: "Авиакомпания: регистрация и бронирование",
      tg_title: "1. Подтверждение через Telegram",
      tg_label: "Telegram @username",
      tg_hint: "Если ты в Telegram WebApp — я подхвачу username автоматически.",
      tg_info_1: "Сначала открой бота и нажми",
      tg_info_2: "Иначе он не сможет писать тебе коды.",
      btn_get_code: "Получить код",

      reg_title: "2. Регистрация пассажира",
      last_name: "Фамилия",
      first_name: "Имя",
      middle_name: "Отчество",
      passport: "Серия и номер паспорта",
      birth_date: "Дата рождения",
      phone: "Телефон",
      email: "E-mail",
      reg_code: "Код из Telegram (регистрация)",
      btn_finish_reg: "Завершить регистрацию",
      reg_ok: "✅ Регистрация завершена. Можно бронировать рейсы.",

      book_title: "3. Бронирование",
      flight: "Рейс",
      price: "Цена (USD)",
      price_hint: "Цена хранится в tickets.price_usd (как ты и хотел).",
      seat_title: "Выбор места",
      btn_reload_seats: "Обновить",
      seat_free: "свободно",
      seat_taken: "занято",
      seat_pick: "выбрано",
      picked: "Выбрано место:",
      btn_get_book_code: "Получить код для бронирования",
      book_code: "Код из Telegram (бронирование)",
      btn_confirm_booking: "Подтвердить бронирование",

      my_title: "Мои рейсы",
      btn_my_flights: "Показать мои рейсы",

      t_code_sent: "Код отправлен в Telegram (если ты нажал /start).",
      t_reg_ok: "Регистрация завершена ✅",
      t_book_code_sent: "Код для бронирования отправлен в Telegram.",
      t_book_ok: "Бронирование подтверждено ✅",
      t_need_seat: "Выбери место.",
      t_need_flight: "Выбери рейс.",
      t_need_username: "Нужен @username.",
      t_bad_username: "Некорректный @username.",
      t_err: "Ошибка. Смотри консоль (и не ори)."
    },
    en:
    {
      title: "Airline: registration and booking",
      tg_title: "1. Telegram confirmation",
      tg_label: "Telegram @username",
      tg_hint: "If you opened this in Telegram WebApp — username will be auto-filled.",
      tg_info_1: "Open the bot and press",
      tg_info_2: "Otherwise it can't DM you codes.",
      btn_get_code: "Get code",

      reg_title: "2. Passenger registration",
      last_name: "Last name",
      first_name: "First name",
      middle_name: "Middle name",
      passport: "Passport ID",
      birth_date: "Birth date",
      phone: "Phone",
      email: "E-mail",
      reg_code: "Telegram code (registration)",
      btn_finish_reg: "Finish registration",
      reg_ok: "✅ Registration complete. You can book flights.",

      book_title: "3. Booking",
      flight: "Flight",
      price: "Price (USD)",
      price_hint: "Stored in tickets.price_usd.",
      seat_title: "Seat selection",
      btn_reload_seats: "Refresh",
      seat_free: "free",
      seat_taken: "taken",
      seat_pick: "picked",
      picked: "Selected seat:",
      btn_get_book_code: "Get booking code",
      book_code: "Telegram code (booking)",
      btn_confirm_booking: "Confirm booking",

      my_title: "My flights",
      btn_my_flights: "Show my flights",

      t_code_sent: "Code was sent to Telegram (if you pressed /start).",
      t_reg_ok: "Registration done ✅",
      t_book_code_sent: "Booking code sent to Telegram.",
      t_book_ok: "Booking confirmed ✅",
      t_need_seat: "Pick a seat.",
      t_need_flight: "Pick a flight.",
      t_need_username: "Need @username.",
      t_bad_username: "Invalid @username.",
      t_err: "Error. Check console."
    }
  };

  function tr(k)
  {
    return (i18n[currentLang] && i18n[currentLang][k]) ? i18n[currentLang][k] : k;
  }

  function applyLang()
  {
    document.querySelectorAll("[data-i18n]").forEach(n =>
    {
      const key = n.getAttribute("data-i18n");
      n.textContent = tr(key);
    });

    el.langBtn.textContent = (currentLang === "ru") ? "EN" : "RU";
  }

  function toast(msg)
  {
    el.toast.textContent = msg;
    el.toast.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() =>
    {
      el.toast.classList.add("hidden");
    }, 2800);
  }

  function normUsername(v)
  {
    v = (v || "").trim();
    if (!v) return "";
    if (!v.startsWith("@")) v = "@" + v;
    return v;
  }

  function isValidUsername(v)
  {
    return /^@[A-Za-z0-9_]{4,32}$/.test(v);
  }

  async function apiFetch(path, options)
  {
    const url = API_BASE + path;
    const res = await fetch(url, options);
    let data = null;
    try
    {
      data = await res.json();
    }
    catch
    {
      data = null;
    }

    if (!res.ok)
    {
      const msg = (data && (data.detail || data.message)) ? (data.detail || data.message) : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function fromTelegramWebApp()
  {
    try
    {
      if (!window.Telegram || !window.Telegram.WebApp) return "";
      const u = window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user;
      if (!u || !u.username) return "";
      return "@" + u.username;
    }
    catch
    {
      return "";
    }
  }

  function unlockBookingUI()
  {
    el.bookingCard.classList.remove("hidden");
    el.myFlightsCard.classList.remove("hidden");
    el.whoami.textContent = el.tgUsername.value ? `(${el.tgUsername.value})` : "";
  }

  function renderFlights()
  {
    el.flightSelect.innerHTML = "";
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "—";
    el.flightSelect.appendChild(opt0);

    flights.forEach(f =>
    {
      const opt = document.createElement("option");
      opt.value = String(f.flight_id);
      opt.textContent = `${f.flight_number} | ${f.departure_city} → ${f.arrival_city} | ${f.flight_date} ${f.flight_time}`;
      opt.dataset.price = String(f.price_suggested);
      opt.dataset.cap = String(f.seat_capacity);
      opt.dataset.plane = f.plane_model;
      el.flightSelect.appendChild(opt);
    });
  }

  function seatButton(seat, taken)
  {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "seat " + (taken ? "taken" : "free");
    b.textContent = seat;
    b.disabled = !!taken;

    b.addEventListener("click", () =>
    {
      if (b.disabled) return;

      document.querySelectorAll(".seat.pick").forEach(x => x.classList.remove("pick"));
      b.classList.add("pick");
      selectedSeat = seat;
      el.pickedSeat.textContent = seat;
    });

    return b;
  }

  function renderSeats(available, taken, cap)
  {
    el.seats.innerHTML = "";
    takenSet = new Set(taken || []);
    capacity = cap || 0;
    selectedSeat = "";
    el.pickedSeat.textContent = "—";

    // чтобы выглядело как самолёт: 6 мест в ряд
    const all = [];
    const letters = ["A","B","C","D","E","F"];
    for (let i = 0; i < capacity; i++)
    {
      const row = Math.floor(i / 6) + 1;
      const seat = row + letters[i % 6];
      all.push(seat);
    }

    all.forEach(seat =>
    {
      const isTaken = takenSet.has(seat);
      el.seats.appendChild(seatButton(seat, isTaken));
    });
  }

  async function loadFlights()
  {
    try
    {
      const data = await apiFetch("/api/flights", { method: "GET" });
      flights = data.flights || [];
      renderFlights();
    }
    catch (e)
    {
      console.error(e);
      toast(tr("t_err"));
    }
  }

  async function loadSeats()
  {
    const flightId = el.flightSelect.value;
    if (!flightId)
    {
      toast(tr("t_need_flight"));
      return;
    }

    try
    {
      const data = await apiFetch(`/api/flights/${flightId}/seats`, { method: "GET" });
      renderSeats(data.available, data.taken, data.capacity);

      const opt = el.flightSelect.selectedOptions[0];
      const plane = opt.dataset.plane || "";
      const cap = opt.dataset.cap || "";
      el.flightMeta.textContent = plane ? `${plane} • ${cap} seats` : "";

      // дефолт цена из API
      const f = flights.find(x => String(x.flight_id) === String(flightId));
      if (f && f.price_suggested)
      {
        el.priceUsd.value = Number(f.price_suggested).toFixed(2);
      }
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  async function getRegCode()
  {
    const u = normUsername(el.tgUsername.value);
    if (!u)
    {
      toast(tr("t_need_username"));
      return;
    }
    if (!isValidUsername(u))
    {
      toast(tr("t_bad_username"));
      return;
    }
    el.tgUsername.value = u;

    try
    {
      await apiFetch("/api/auth/start",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram_username: u, purpose: "register" })
      });
      toast(tr("t_code_sent"));
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  async function finishRegistration()
  {
    const u = normUsername(el.tgUsername.value);
    if (!u)
    {
      toast(tr("t_need_username"));
      return;
    }
    if (!isValidUsername(u))
    {
      toast(tr("t_bad_username"));
      return;
    }

    const payload =
    {
      telegram_username: u,
      code: (el.regCode.value || "").trim(),
      last_name: (el.lastName.value || "").trim(),
      first_name: (el.firstName.value || "").trim(),
      middle_name: (el.middleName.value || "").trim(),
      passport_no: (el.passportNo.value || "").trim(),
      birth_date: (el.birthDate.value || "").trim(),
      phone: (el.phone.value || "").trim(),
      email: (el.email.value || "").trim()
    };

    try
    {
      await apiFetch("/api/register/complete",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      localStorage.setItem("tg_username", u);

      el.regOk.classList.remove("hidden");
      toast(tr("t_reg_ok"));
      unlockBookingUI();

      await loadFlights();
      el.flightSelect.value = "";
      el.bookConfirmRow.classList.add("hidden");
      el.bookCode.value = "";
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  async function getBookingCode()
  {
    const u = normUsername(el.tgUsername.value);
    if (!u || !isValidUsername(u))
    {
      toast(tr("t_need_username"));
      return;
    }

    const flightId = el.flightSelect.value;
    if (!flightId)
    {
      toast(tr("t_need_flight"));
      return;
    }

    if (!selectedSeat)
    {
      toast(tr("t_need_seat"));
      return;
    }

    const price = Number(el.priceUsd.value || "0");
    if (!isFinite(price) || price <= 0)
    {
      toast("Цена должна быть > 0");
      return;
    }

    try
    {
      await apiFetch("/api/booking/start",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
        {
          telegram_username: u,
          flight_id: Number(flightId),
          seat_no: selectedSeat,
          price_usd: price
        })
      });

      el.bookConfirmRow.classList.remove("hidden");
      toast(tr("t_book_code_sent"));
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  async function confirmBooking()
  {
    const u = normUsername(el.tgUsername.value);
    const code = (el.bookCode.value || "").trim();

    try
    {
      await apiFetch("/api/booking/confirm",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram_username: u, code })
      });

      toast(tr("t_book_ok"));
      el.bookConfirmRow.classList.add("hidden");
      el.bookCode.value = "";
      await loadSeats();
      await loadMyFlights();
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  function flightCard(item)
  {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML =
      `<div class="item-top">
         <div class="item-title">${item.flight_number} • ${item.route}</div>
         <div class="item-sub">${item.dt} • ${item.plane_model}</div>
       </div>
       <div class="item-bottom">
         <span><b>Seat:</b> ${item.seat_no}</span>
         <span><b>Price:</b> $${Number(item.price_usd).toFixed(2)}</span>
       </div>`;
    return d;
  }

  async function loadMyFlights()
  {
    const u = normUsername(el.tgUsername.value);
    if (!u || !isValidUsername(u))
    {
      toast(tr("t_need_username"));
      return;
    }

    try
    {
      const data = await apiFetch(`/api/my/flights?telegram_username=${encodeURIComponent(u)}`, { method: "GET" });
      const items = data.items || [];
      el.myFlights.innerHTML = "";

      if (!items.length)
      {
        el.myFlights.innerHTML = `<div class="muted">Пока пусто. Забронируй что-нибудь.</div>`;
        return;
      }

      items.forEach(x => el.myFlights.appendChild(flightCard(x)));
    }
    catch (e)
    {
      console.error(e);
      toast(e.message || tr("t_err"));
    }
  }

  function setupTelegram()
  {
    const tgUser = fromTelegramWebApp();
    const stored = localStorage.getItem("tg_username") || "";
    const u = tgUser || stored;

    if (u)
    {
      el.tgUsername.value = u;
      if (tgUser)
      {
        el.tgUsername.disabled = true;
      }
    }

    try
    {
      if (window.Telegram && window.Telegram.WebApp)
      {
        window.Telegram.WebApp.expand();
      }
    }
    catch {}
  }

  function setup()
  {
    setupTelegram();

    applyLang();

    el.langBtn.addEventListener("click", () =>
    {
      currentLang = (currentLang === "ru") ? "en" : "ru";
      applyLang();
    });

    el.btnGetRegCode.addEventListener("click", getRegCode);
    el.btnRegister.addEventListener("click", finishRegistration);

    el.flightSelect.addEventListener("change", async () =>
    {
      el.bookConfirmRow.classList.add("hidden");
      el.bookCode.value = "";
      await loadSeats();
    });

    el.btnReloadSeats.addEventListener("click", loadSeats);
    el.btnGetBookCode.addEventListener("click", getBookingCode);
    el.btnConfirmBooking.addEventListener("click", confirmBooking);

    el.btnMyFlights.addEventListener("click", loadMyFlights);

    // Если username уже есть — покажем блоки, но только после регистрации это реально осмысленно
    if (el.tgUsername.value)
    {
      el.whoami.textContent = `(${el.tgUsername.value})`;
    }

    loadFlights();
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
