// script.js
// Бэкенд зашит (ngrok домен). Никаких модалок "введи URL".

const DEFAULT_API = "https://kristan-labored-earsplittingly.ngrok-free.dev";

const $ = (id) => document.getElementById(id);

const toastEl = $("toast");
let toastTimer = null;

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 2600);
}

function normUser(u) {
  u = (u || "").trim();
  if (!u) return "";
  return u.startsWith("@") ? u : "@" + u;
}

async function apiFetch(path, opts = {}) {
  const url = DEFAULT_API + path;

  const res = await fetch(url, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });

  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : null;

  if (!res.ok) {
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

function tryAutoFillTelegramUsername() {
  try {
    const tg = window.Telegram?.WebApp;
    const u = tg?.initDataUnsafe?.user?.username;
    if (u) {
      $("reg-username").value = "@" + u;
      $("login-username").value = "@" + u;
    }
  } catch (_) {}
}

function saveAuth(username) {
  localStorage.setItem("airline_username", username);
}
function loadAuth() {
  return localStorage.getItem("airline_username") || "";
}
function clearAuth() {
  localStorage.removeItem("airline_username");
}

function showAuth() {
  $("view-auth").classList.remove("hidden");
  $("view-booking").classList.add("hidden");
}
function showBooking(username) {
  $("view-auth").classList.add("hidden");
  $("view-booking").classList.remove("hidden");
  $("whoami").textContent = username;
}

function switchTab(tab) {
  const regTab = $("tab-register");
  const loginTab = $("tab-login");
  const regPane = $("pane-register");
  const loginPane = $("pane-login");

  if (tab === "register") {
    regTab.classList.add("active");
    loginTab.classList.remove("active");
    regPane.classList.remove("hidden");
    loginPane.classList.add("hidden");
  } else {
    loginTab.classList.add("active");
    regTab.classList.remove("active");
    loginPane.classList.remove("hidden");
    regPane.classList.add("hidden");
  }
}

// ==========================
// AUTH
// ==========================

async function regGetCode() {
  const username = normUser($("reg-username").value);
  if (!username) return toast("Введи @username.");

  await apiFetch("/api/auth/request-code", {
    method: "POST",
    body: JSON.stringify({ username, purpose: "register" }),
  });

  toast("Код отправлен в Telegram.");
}

async function regConfirm() {
  const username = normUser($("reg-username").value);
  const code = ($("reg-code").value || "").trim();

  const last_name = ($("reg-last").value || "").trim();
  const first_name = ($("reg-first").value || "").trim();
  const middle_name = ($("reg-middle").value || "").trim();
  const passport_no = ($("reg-passport").value || "").trim();
  const phone = ($("reg-phone").value || "").trim();
  const email = ($("reg-email").value || "").trim();

  if (!username) return toast("Введи @username.");
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр.");
  if (!last_name || !first_name || !passport_no || !phone || !email) return toast("Заполни все поля (кроме отчества).");

  await apiFetch("/api/auth/confirm-register", {
    method: "POST",
    body: JSON.stringify({ username, code, last_name, first_name, middle_name, passport_no, phone, email }),
  });

  saveAuth(username);
  toast("Регистрация успешна.");
  showBooking(username);
}

async function loginGetCode() {
  const username = normUser($("login-username").value);
  if (!username) return toast("Введи @username.");

  await apiFetch("/api/auth/request-code", {
    method: "POST",
    body: JSON.stringify({ username, purpose: "login" }),
  });

  toast("Код отправлен в Telegram.");
}

async function loginConfirm() {
  const username = normUser($("login-username").value);
  const code = ($("login-code").value || "").trim();

  if (!username) return toast("Введи @username.");
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр.");

  await apiFetch("/api/auth/confirm-login", {
    method: "POST",
    body: JSON.stringify({ username, code }),
  });

  saveAuth(username);
  toast("Вход выполнен.");
  showBooking(username);
}

// ==========================
// BOOKING
// ==========================

let flightsOut = [];
let flightsBack = [];
let selectedOut = null;
let selectedBack = null;
let seatOut = "";
let seatBack = "";

function setReturnEnabled(enabled) {
  $("date-back").disabled = !enabled;
  $("back-title").classList.toggle("muted", !enabled);
  $("seats-back-title").classList.toggle("muted", !enabled);
  if (!enabled) {
    flightsBack = [];
    selectedBack = null;
    seatBack = "";
    $("list-back").innerHTML = "";
    $("seats-back").innerHTML = "";
  }
}

function flightCard(f, active) {
  const el = document.createElement("div");
  el.className = "item" + (active ? " active" : "");
  el.innerHTML = `
    <div class="line"><span>${f.flight_number}</span><span>$${f.price_usd}</span></div>
    <div class="meta">
      ${f.departure_city} → ${f.arrival_city}<br/>
      ${f.flight_date} ${f.flight_time} • ${f.seat_capacity} мест
    </div>
  `;
  return el;
}

async function loadSeats(flightId, targetElId, chosenSeatSetter) {
  const data = await apiFetch(`/api/flights/${flightId}/seats`);
  const seats = data.seats || [];

  const box = document.createElement("div");
  box.className = "seat-grid";

  seats.forEach((s) => {
    const b = document.createElement("div");
    b.className = "seat " + (s.status || "free");
    if (s.status !== "free") b.classList.add("disabled");
    b.textContent = s.seat_no;

    b.onclick = () => {
      if (s.status !== "free") return;
      box.querySelectorAll(".seat").forEach((x) => x.classList.remove("selected"));
      b.classList.add("selected");
      chosenSeatSetter(s.seat_no);
      renderSummary();
    };

    box.appendChild(b);
  });

  $(targetElId).innerHTML = "";
  $(targetElId).appendChild(box);
}

function renderFlights(listId, flights, selected, onPick) {
  const box = $(listId);
  box.innerHTML = "";
  flights.forEach((f) => {
    const el = flightCard(f, selected && selected.flight_id === f.flight_id);
    el.onclick = () => onPick(f);
    box.appendChild(el);
  });
}

function renderSummary() {
  let txt = "";
  if (selectedOut) {
    txt += `Туда: ${selectedOut.flight_number} (${selectedOut.flight_date} ${selectedOut.flight_time})\n`;
    txt += `Место: ${seatOut || "—"} • Цена: $${selectedOut.price_usd}\n\n`;
  } else {
    txt += "Туда: —\n\n";
  }

  const backEnabled = $("has-return").checked;
  if (backEnabled) {
    if (selectedBack) {
      txt += `Назад: ${selectedBack.flight_number} (${selectedBack.flight_date} ${selectedBack.flight_time})\n`;
      txt += `Место: ${seatBack || "—"} • Цена: $${selectedBack.price_usd}\n`;
    } else {
      txt += "Назад: —\n";
    }
  } else {
    txt += "Назад: —\n";
  }

  $("summary").textContent = txt.trim();
}

async function searchFlights() {
  const from_country = ($("from-country").value || "").trim();
  const from_city = ($("from-city").value || "").trim();
  const to_country = ($("to-country").value || "").trim();
  const to_city = ($("to-city").value || "").trim();
  const date_out = $("date-out").value;

  if (!from_city || !to_city || !date_out) return toast("Заполни города и дату туда.");

  const out = await apiFetch(
    `/api/flights/search?from_city=${encodeURIComponent(from_city)}&from_country=${encodeURIComponent(from_country)}&to_city=${encodeURIComponent(to_city)}&to_country=${encodeURIComponent(to_country)}&date=${encodeURIComponent(date_out)}`
  );

  flightsOut = out.flights || [];
  selectedOut = null;
  seatOut = "";
  $("seats-out").innerHTML = "";

  const pickOut = async (f) => {
    selectedOut = f;
    seatOut = "";
    renderFlights("list-out", flightsOut, selectedOut, pickOut);
    await loadSeats(f.flight_id, "seats-out", (s) => (seatOut = s));
    renderSummary();
  };

  renderFlights("list-out", flightsOut, selectedOut, pickOut);

  const backEnabled = $("has-return").checked;
  if (backEnabled) {
    const date_back = $("date-back").value;
    if (!date_back) {
      toast("Выбрал дату назад — так выбери её.");
    } else {
      const back = await apiFetch(
        `/api/flights/search?from_city=${encodeURIComponent(to_city)}&from_country=${encodeURIComponent(to_country)}&to_city=${encodeURIComponent(from_city)}&to_country=${encodeURIComponent(from_country)}&date=${encodeURIComponent(date_back)}`
      );

      flightsBack = back.flights || [];
      selectedBack = null;
      seatBack = "";
      $("seats-back").innerHTML = "";

      const pickBack = async (f) => {
        selectedBack = f;
        seatBack = "";
        renderFlights("list-back", flightsBack, selectedBack, pickBack);
        await loadSeats(f.flight_id, "seats-back", (s) => (seatBack = s));
        renderSummary();
      };

      renderFlights("list-back", flightsBack, selectedBack, pickBack);
    }
  } else {
    flightsBack = [];
    selectedBack = null;
    seatBack = "";
    $("list-back").innerHTML = "";
    $("seats-back").innerHTML = "";
  }

  $("results").classList.remove("hidden");
  $("my").classList.add("hidden");
  renderSummary();
}

function buildBookingSelections() {
  const sels = [];
  if (!selectedOut || !seatOut) return null;

  sels.push({ flight_id: selectedOut.flight_id, seat_no: seatOut, price_usd: selectedOut.price_usd });

  const backEnabled = $("has-return").checked;
  if (backEnabled) {
    if (!selectedBack || !seatBack) return null;
    sels.push({ flight_id: selectedBack.flight_id, seat_no: seatBack, price_usd: selectedBack.price_usd });
  }
  return sels;
}

async function bookingGetCode() {
  const username = loadAuth();
  if (!username) return toast("Сначала войди.");

  const selections = buildBookingSelections();
  if (!selections) return toast("Выбери рейс(ы) и место(а).");

  await apiFetch("/api/booking/request-code", {
    method: "POST",
    body: JSON.stringify({ username, selections }),
  });

  toast("Код бронирования отправлен в Telegram.");
}

async function bookingConfirm() {
  const username = loadAuth();
  const code = ($("book-code").value || "").trim();
  if (!username) return toast("Сначала войди.");
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр.");

  await apiFetch("/api/booking/confirm", {
    method: "POST",
    body: JSON.stringify({ username, code }),
  });

  toast("Бронирование подтверждено ✅");

  if (selectedOut) await loadSeats(selectedOut.flight_id, "seats-out", (s) => (seatOut = s));
  if (selectedBack) await loadSeats(selectedBack.flight_id, "seats-back", (s) => (seatBack = s));

  seatOut = "";
  seatBack = "";
  renderSummary();
}

async function loadMyFlights() {
  const username = loadAuth();
  if (!username) return toast("Сначала войди.");

  const data = await apiFetch(`/api/my-flights?username=${encodeURIComponent(username)}`);
  const list = data.tickets || [];

  const box = $("my-list");
  box.innerHTML = "";

  if (list.length === 0) {
    box.innerHTML = `<div class="item"><div class="meta">Пусто. У тебя нет бронирований.</div></div>`;
  } else {
    list.forEach((t) => {
      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `
        <div class="line"><span>${t.flight_number}</span><span>$${t.price_usd}</span></div>
        <div class="meta">
          ${t.from} → ${t.to}<br/>
          ${t.date} ${t.time} • место ${t.seat_no} • статус: ${t.status}
        </div>
      `;
      box.appendChild(el);
    });
  }

  $("my").classList.remove("hidden");
  toast("Показала твои брони.");
}

// ==========================
// INIT
// ==========================

function bind() {
  $("tab-register").onclick = () => switchTab("register");
  $("tab-login").onclick = () => switchTab("login");

  $("reg-get-code").onclick = async () => {
    try { await regGetCode(); } catch (e) { toast(String(e.message || e)); }
  };
  $("reg-confirm").onclick = async () => {
    try { await regConfirm(); } catch (e) { toast(String(e.message || e)); }
  };

  $("login-get-code").onclick = async () => {
    try { await loginGetCode(); } catch (e) { toast(String(e.message || e)); }
  };
  $("login-confirm").onclick = async () => {
    try { await loginConfirm(); } catch (e) { toast(String(e.message || e)); }
  };

  $("has-return").onchange = () => setReturnEnabled($("has-return").checked);

  $("btn-search").onclick = async () => {
    try { await searchFlights(); } catch (e) { toast(String(e.message || e)); }
  };

  $("book-get-code").onclick = async () => {
    try { await bookingGetCode(); } catch (e) { toast(String(e.message || e)); }
  };

  $("book-confirm").onclick = async () => {
    try { await bookingConfirm(); } catch (e) { toast(String(e.message || e)); }
  };

  $("btn-my").onclick = async () => {
    try { await loadMyFlights(); } catch (e) { toast(String(e.message || e)); }
  };

  $("btn-logout").onclick = () => {
    clearAuth();
    toast("Вышел.");
    showAuth();
  };
}

(function init() {
  bind();
  tryAutoFillTelegramUsername();

  const u = loadAuth();
  if (u) showBooking(u);
  else showAuth();

  setReturnEnabled(false);
  renderSummary();
})();
