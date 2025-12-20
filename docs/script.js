// ===== CONFIG =====
// По умолчанию — твой ngrok. Можно тихо переопределить через ?api=https://.... (без модалок и истерик).
let API_BASE = "https://kristan-labored-earsplittingly.ngrok-free.dev";

(function pickApiFromQuery() {
  const p = new URLSearchParams(location.search);
  const v = (p.get("api") || "").trim();
  if (v.startsWith("http")) {
    API_BASE = v.replace(/\/+$/, "");
    localStorage.setItem("air_api_base", API_BASE);
  } else {
    const saved = (localStorage.getItem("air_api_base") || "").trim();
    if (saved.startsWith("http")) API_BASE = saved.replace(/\/+$/, "");
  }
})();

const $ = (id) => document.getElementById(id);

function toast(msg, ok = false) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  t.classList.toggle("ok", ok);
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);

  const url = API_BASE + path;

  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new Error("Failed to fetch");
  }

  const ct = (res.headers.get("content-type") || "").toLowerCase();

  if (!ct.includes("application/json")) {
    const txt = await res.text().catch(() => "");
    if (!res.ok) throw new Error(txt || ("HTTP " + res.status));
    throw new Error("Сервер вернул не JSON (Content-Type: " + ct + ")");
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }

  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) ? (data.detail || data.message) : ("HTTP " + res.status);
    throw new Error(msg);
  }

  if (data === null) {
    throw new Error("Сервер вернул пустой/битый JSON");
  }

  return data;
}

function normU(u) {
  u = (u || "").trim();
  if (!u) return "";
  if (!u.startsWith("@")) u = "@" + u;
  return u;
}

function setToken(token) { localStorage.setItem("air_token", token); }
function getToken() { return localStorage.getItem("air_token") || ""; }
function clearToken() { localStorage.removeItem("air_token"); }

function showAuth() {
  $("auth").classList.remove("hidden");
  $("app").classList.add("hidden");
  $("btnLogout").classList.add("hidden");
}
function showApp() {
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("btnLogout").classList.remove("hidden");
}

function showModal(on) { $("modal").classList.toggle("hidden", !on); }
function showModal2(on) { $("modal2").classList.toggle("hidden", !on); }

// ===== Tabs =====
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("tab-register").classList.toggle("hidden", tab !== "register");
    $("tab-login").classList.toggle("hidden", tab !== "login");
  });
});

// ===== AUTH: Register =====
$("btnRegCode").addEventListener("click", async () => {
  const username = normU($("regUsername").value);
  if (!username) return toast("Введи Telegram @username");

  try {
    await api("/api/auth/request-code", "POST", { username, purpose: "register" });
    toast("Код отправлен в Telegram ✅", true);
  } catch (e) {
    toast(e.message);
  }
});

$("btnRegConfirm").addEventListener("click", async () => {
  const username = normU($("regUsername").value);
  const code = ($("regCode").value || "").trim();

  const last_name = ($("regLast").value || "").trim();
  const first_name = ($("regFirst").value || "").trim();
  const middle_name = ($("regMiddle").value || "").trim();
  const passport_no = ($("regPassport").value || "").trim();
  const phone = ($("regPhone").value || "").trim();
  const email = ($("regEmail").value || "").trim();

  if (!username) return toast("Нет @username");
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр");
  if (!last_name || !first_name || !passport_no || !phone || !email) return toast("Заполни обязательные поля");

  try {
    const data = await api("/api/auth/confirm-register", "POST", {
      username, code,
      last_name, first_name,
      middle_name: middle_name || null,
      passport_no, phone, email
    });

    setToken(data.token);
    toast("Регистрация подтверждена ✅", true);
    showApp();
    await searchFlights();
  } catch (e) {
    toast(e.message);
  }
});

// ===== AUTH: Login =====
$("btnLoginCode").addEventListener("click", async () => {
  const username = normU($("loginUsername").value);
  if (!username) return toast("Введи Telegram @username");

  try {
    await api("/api/auth/request-code", "POST", { username, purpose: "login" });
    toast("Код отправлен в Telegram ✅", true);
  } catch (e) {
    toast(e.message);
  }
});

$("btnLoginConfirm").addEventListener("click", async () => {
  const username = normU($("loginUsername").value);
  const code = ($("loginCode").value || "").trim();

  if (!username) return toast("Нет @username");
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр");

  try {
    const data = await api("/api/auth/confirm-login", "POST", { username, code });
    setToken(data.token);
    toast("Вход выполнен ✅", true);
    showApp();
    await searchFlights();
  } catch (e) {
    toast(e.message);
  }
});

// ===== App =====
$("btnLogout").addEventListener("click", () => {
  clearToken();
  showAuth();
  toast("Вышел.");
});

$("btnSearch").addEventListener("click", async () => {
  await searchFlights();
});

async function searchFlights() {
  const dep = ($("fDep").value || "").trim();
  const arr = ($("fArr").value || "").trim();
  const date_from = $("fDateFrom").value || "";
  const date_to = $("fDateTo").value || "";

  const list = $("flightsList");
  list.innerHTML = `<div class="muted">Ищу рейсы...</div>`;

  try {
    const data = await api("/api/flights/search", "POST", {
      dep: dep || null,
      arr: arr || null,
      date_from: date_from || null,
      date_to: date_to || null,
      limit: 120
    });

    const flights = (data && Array.isArray(data.flights)) ? data.flights : [];
    if (!flights.length) {
      list.innerHTML = `<div class="muted">Ничего не найдено. Попробуй другие фильтры.</div>`;
      return;
    }

    list.innerHTML = "";
    flights.forEach(f => list.appendChild(flightCard(f)));
  } catch (e) {
    list.innerHTML = `<div class="muted">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
}

function flightCard(f) {
  const el = document.createElement("div");
  el.className = "flight";

  el.innerHTML = `
    <div class="flightTop">
      <div class="fn">Рейс ${escapeHtml(f.flight_number)}</div>
      <div class="price">$${Number(f.suggested_price).toFixed(2)}</div>
    </div>

    <div class="route">
      <div>
        <div class="city">${escapeHtml(f.dep)}</div>
        <div class="dt">${escapeHtml(f.date)} ${escapeHtml(f.time)}</div>
      </div>
      <div class="arrow">→</div>
      <div>
        <div class="city">${escapeHtml(f.arr)}</div>
        <div class="dt">${escapeHtml(f.plane_model)} · ${Number(f.seat_capacity)} мест</div>
      </div>
    </div>

    <div class="row end">
      <button class="btn primary">Выбрать</button>
    </div>
  `;

  el.querySelector("button").addEventListener("click", () => openSeatModal(f));
  return el;
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ===== Seat Modal =====
let currentFlight = null;
let selectedSeat = "";
let lastBookingRequestId = "";

$("mClose").addEventListener("click", () => showModal(false));
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") showModal(false); });

$("btnBookCode").addEventListener("click", async () => {
  if (!currentFlight) return toast("Нет рейса");
  if (!selectedSeat) return toast("Выбери место");

  const token = getToken();
  if (!token) return toast("Сессии нет. Выйди и зайди заново.");

  const price = Number($("mPrice").value || "0");
  if (!(price > 0)) return toast("Цена должна быть > 0");

  try {
    const data = await api("/api/booking/request", "POST", {
      token,
      flight_id: currentFlight.flight_id,
      seat_no: selectedSeat,
      price_usd: price
    });
    lastBookingRequestId = data.request_id;
    toast("Код бронирования отправлен в Telegram ✅", true);
  } catch (e) {
    toast(e.message);
  }
});

$("btnBookConfirm").addEventListener("click", async () => {
  const token = getToken();
  if (!token) return toast("Сессии нет.");
  if (!lastBookingRequestId) return toast("Сначала получи код бронирования");

  const code = ($("bookCode").value || "").trim();
  if (!/^\d{6}$/.test(code)) return toast("Код — 6 цифр");

  try {
    await api("/api/booking/confirm", "POST", {
      token,
      request_id: lastBookingRequestId,
      code
    });

    toast("Бронь подтверждена ✅", true);
    showModal(false);
    await searchFlights();
  } catch (e) {
    toast(e.message);
  }
});

async function openSeatModal(f) {
  currentFlight = f;
  selectedSeat = "";
  lastBookingRequestId = "";
  $("bookCode").value = "";

  $("mTitle").textContent = `Рейс ${f.flight_number}`;
  $("mSub").textContent = `${f.dep} → ${f.arr} · ${f.date} ${f.time} · ${f.plane_model}`;
  $("mPrice").value = Number(f.suggested_price).toFixed(2);
  $("mSeat").textContent = "—";

  const grid = $("seatGrid");
  grid.innerHTML = `<div class="muted">Загружаю места...</div>`;

  showModal(true);

  try {
    const data = await api(`/api/flights/${f.flight_id}/seats`, "GET");
    const seats = (data && Array.isArray(data.seats)) ? data.seats : [];
    renderSeats(seats);
  } catch (e) {
    grid.innerHTML = `<div class="muted">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
}

function renderSeats(seats) {
  const grid = $("seatGrid");
  grid.innerHTML = "";

  if (!seats.length) {
    grid.innerHTML = `<div class="muted">Нет мест (что-то очень странное).</div>`;
    return;
  }

  seats.forEach(x => {
    const seat = String(x.seat || "");
    const status = String(x.status || "free");

    const b = document.createElement("button");
    b.className = "seat " + (status === "booked" ? "booked" : "free");
    b.textContent = seat;

    if (status === "booked") {
      b.disabled = true;
    } else {
      b.addEventListener("click", () => {
        grid.querySelectorAll(".seat.pick").forEach(s => s.classList.remove("pick"));
        b.classList.add("pick");
        selectedSeat = seat;
        $("mSeat").textContent = selectedSeat;
      });
    }

    grid.appendChild(b);
  });
}

// ===== My flights =====
$("btnMyFlights").addEventListener("click", async () => {
  const token = getToken();
  if (!token) return toast("Сессии нет.");

  $("myFlightsList").innerHTML = `<div class="muted">Загружаю...</div>`;
  showModal2(true);

  try {
    const data = await api(`/api/me/flights?token=${encodeURIComponent(token)}`, "GET");
    const flights = (data && Array.isArray(data.flights)) ? data.flights : [];

    if (!flights.length) {
      $("myFlightsList").innerHTML = `<div class="muted">Пока пусто. Забронируй что-нибудь.</div>`;
      return;
    }

    $("myFlightsList").innerHTML = "";
    flights.forEach(t => {
      const el = document.createElement("div");
      el.className = "flight";
      el.innerHTML = `
        <div class="flightTop">
          <div class="fn">Рейс ${escapeHtml(t.flight_number)}</div>
          <div class="price">$${Number(t.price_usd).toFixed(2)}</div>
        </div>
        <div class="route">
          <div>
            <div class="city">${escapeHtml(t.dep)}</div>
            <div class="dt">${escapeHtml(t.date)} ${escapeHtml(t.time)}</div>
          </div>
          <div class="arrow">→</div>
          <div>
            <div class="city">${escapeHtml(t.arr)}</div>
            <div class="dt">${escapeHtml(t.plane_model)} · место <b>${escapeHtml(t.seat_no)}</b></div>
          </div>
        </div>
      `;
      $("myFlightsList").appendChild(el);
    });
  } catch (e) {
    $("myFlightsList").innerHTML = `<div class="muted">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
});

$("m2Close").addEventListener("click", () => showModal2(false));
$("modal2").addEventListener("click", (e) => { if (e.target.id === "modal2") showModal2(false); });

// ===== Boot =====
(async function boot() {
  if (getToken()) {
    showApp();
    await searchFlights();
  } else {
    showAuth();
  }
})();
