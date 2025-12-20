// ---------------------------
// CONFIG
// ---------------------------
// НЕ ручной ввод в UI. Если хочешь поменять адрес — меняешь тут один раз.
const API_BASE = "https://kristan-labored-earsplittingly.ngrok-free.dev";

// ---------------------------
// helpers
// ---------------------------
const $ = (id) => document.getElementById(id);

function show(el, yes) { el.style.display = yes ? "" : "none"; }
function err(el, msg) { el.textContent = msg; show(el, !!msg); }

function normUser(u) {
  u = (u || "").trim();
  if (!u) return "";
  if (!u.startsWith("@")) u = "@" + u;
  return u.toLowerCase();
}

async function api(path, method="GET", body=null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(API_BASE + path, opts);
  let data = null;
  try { data = await res.json(); } catch { data = null; }

  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) ? (data.detail || data.message) : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

// ---------------------------
// state
// ---------------------------
let currentUser = localStorage.getItem("air_user") || "";
let authMode = ""; // "login"|"register"
let selectedFlight = null; // {flight_id,...}
let selectedSeat = "";
let selectedPrice = 0;

// ---------------------------
// UI switching
// ---------------------------
function setScreen(name) {
  show($("authSection"), name === "auth");
  show($("searchSection"), name === "search");
  show($("seatsSection"), name === "seats");
  show($("myFlightsSection"), name === "myflights");

  const authed = !!currentUser;
  $("myFlightsBtn").disabled = !authed;
  $("logoutBtn").disabled = !authed;
}

// ---------------------------
// Auth
// ---------------------------
async function startCode(purpose) {
  const username = normUser($("username").value);
  if (!username) return err($("authErr"), "Впиши @username.");

  err($("authErr"), "");

  await api("/api/auth/start", "POST", { username, purpose, payload: {} });

  if (purpose === "register") {
    authMode = "register";
    show($("regForm"), true);
    err($("authErr"), "Код отправлен в Telegram. Введи его ниже и заполни данные пассажира.");
  } else {
    authMode = "login";
    show($("regForm"), false);
    err($("authErr"), "Код отправлен в Telegram. Введи его и жми «Войти».");
  }
}

async function doLogin() {
  const username = normUser($("username").value);
  const code = ($("codeAuth").value || "").trim();

  err($("authErr"), "");

  await api("/api/auth/verify", "POST", { username, purpose: "login", code });

  currentUser = username;
  localStorage.setItem("air_user", currentUser);

  setScreen("search");
}

async function finishRegister() {
  const username = normUser($("username").value);
  const code = ($("codeReg").value || "").trim();

  err($("authErr"), "");

  // verify code
  await api("/api/auth/verify", "POST", { username, purpose: "register", code });

  // save passenger
  const p = {
    username,
    last_name: ($("lastName").value || "").trim(),
    first_name: ($("firstName").value || "").trim(),
    middle_name: ($("middleName").value || "").trim(),
    passport_no: ($("passport").value || "").trim(),
    phone: ($("phone").value || "").trim(),
    email: ($("email").value || "").trim(),
  };

  if (!p.last_name || !p.first_name || !p.passport_no || !p.phone || !p.email) {
    return err($("authErr"), "Заполни данные пассажира полностью (кроме отчества).");
  }

  await api("/api/passengers/register", "POST", p);

  currentUser = username;
  localStorage.setItem("air_user", currentUser);

  setScreen("search");
}

// ---------------------------
// Search flights
// ---------------------------
function renderFlights(list, container, kind) {
  container.innerHTML = "";
  if (!list.length) {
    container.innerHTML = `<div class="muted">Ничего не найдено.</div>`;
    return;
  }

  for (const f of list) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="itemTop">
        <div class="big">${f.flight_number}</div>
        <div class="price">${f.price_usd} USD</div>
      </div>
      <div class="muted">${f.from} → ${f.to}</div>
      <div class="muted">${f.date} ${f.time} • ${f.seat_capacity} мест</div>
      <div class="row">
        <button class="btn small">Выбрать</button>
      </div>
    `;
    div.querySelector("button").onclick = () => chooseFlight(f);
    container.appendChild(div);
  }
}

async function doSearch() {
  err($("searchErr"), "");
  const from_city = ($("fromCity").value || "").trim();
  const to_city = ($("toCity").value || "").trim();
  const date_out = ($("dateOut").value || "").trim();
  let date_back = ($("dateBack").value || "").trim();

  if (date_back === "-") date_back = "";

  try {
    const data = await api("/api/flights/search", "POST", { from_city, to_city, date_out, date_back });

    show($("resultsBox"), true);
    renderFlights(data.outbound || [], $("outboundList"), "out");

    if ((data.return || []).length) {
      show($("returnBlock"), true);
      renderFlights(data.return || [], $("returnList"), "back");
    } else {
      show($("returnBlock"), false);
      $("returnList").innerHTML = "";
    }

  } catch (e) {
    err($("searchErr"), e.message);
  }
}

// ---------------------------
// Seats
// ---------------------------
function seatStateClass(st) {
  if (st === "reserved") return "seat reserved";
  if (st === "purchased") return "seat purchased";
  return "seat free";
}

function renderSeatMap(mapObj, cap) {
  const seatMap = $("seatMap");
  seatMap.innerHTML = "";

  // numbers 10/20/30
  const nums = cap / 6;
  const letters = ["A","B","C","D","E","F"];

  // grid: rows = letters, cols = numbers
  const grid = document.createElement("div");
  grid.className = "seatgrid";
  grid.style.gridTemplateColumns = `repeat(${nums}, 1fr)`;

  // We place seat blocks by columns: A1..F1 stacked? user said behind A1 is B1 etc.
  // Visual: each column = number, inside it 6 seats A..F vertically.
  for (let n = 1; n <= nums; n++) {
    const col = document.createElement("div");
    col.className = "seatcol";
    const head = document.createElement("div");
    head.className = "colhead";
    head.textContent = n;
    col.appendChild(head);

    for (const L of letters) {
      const seat = `${L}${n}`;
      const st = mapObj[seat] || "free";

      const b = document.createElement("button");
      b.className = seatStateClass(st);
      b.textContent = L;

      if (st !== "free") {
        b.disabled = true;
      }

      b.onclick = () => {
        selectedSeat = seat;
        $("chosenSeat").textContent = seat;
        $("sendBookingCode").disabled = false;
        $("confirmBooking").disabled = false;

        // highlight chosen
        document.querySelectorAll(".seat").forEach(x => x.classList.remove("chosen"));
        b.classList.add("chosen");
      };

      col.appendChild(b);
    }

    grid.appendChild(col);
  }

  seatMap.appendChild(grid);
}

async function chooseFlight(f) {
  selectedFlight = f;
  selectedSeat = "";
  selectedPrice = f.price_usd;

  $("chosenFlightHint").textContent = `${f.flight_number}: ${f.from} → ${f.to}, ${f.date} ${f.time}, самолёт на ${f.seat_capacity} мест`;
  $("chosenSeat").textContent = "—";
  $("chosenPrice").textContent = String(selectedPrice);

  $("sendBookingCode").disabled = true;
  $("confirmBooking").disabled = true;
  $("codeBook").value = "";

  setScreen("seats");
  await refreshSeats();
}

async function refreshSeats() {
  err($("seatErr"), "");
  if (!selectedFlight) return;

  try {
    const data = await api(`/api/flights/${selectedFlight.flight_id}/seats`, "GET");
    renderSeatMap(data.map || {}, data.seat_capacity);
  } catch (e) {
    err($("seatErr"), e.message);
  }
}

// ---------------------------
// Booking
// ---------------------------
async function sendBookingCode() {
  err($("seatErr"), "");
  if (!currentUser) return err($("seatErr"), "Ты не вошёл.");
  if (!selectedFlight) return err($("seatErr"), "Рейс не выбран.");
  if (!selectedSeat) return err($("seatErr"), "Выбери место.");

  try {
    await api("/api/booking/start", "POST", {
      username: currentUser,
      flight_id: selectedFlight.flight_id,
      seat_no: selectedSeat,
      price_usd: selectedPrice
    });
    err($("seatErr"), "Код бронирования отправлен в Telegram. Введи его и подтверди.");
  } catch (e) {
    err($("seatErr"), e.message);
  }
}

async function confirmBooking() {
  err($("seatErr"), "");
  const code = ($("codeBook").value || "").trim();
  try {
    await api("/api/booking/confirm", "POST", {
      username: currentUser,
      purpose: "booking",
      code
    });
    err($("seatErr"), "✅ Забронировано. Место теперь занято навсегда (поздравляю).");
    await refreshSeats();
  } catch (e) {
    err($("seatErr"), e.message);
  }
}

// ---------------------------
// My flights
// ---------------------------
async function showMyFlights() {
  err($("myFlightsErr"), "");
  $("myFlightsList").innerHTML = "";
  setScreen("myflights");

  try {
    const list = await api(`/api/my-flights?username=${encodeURIComponent(currentUser)}`, "GET");
    if (!list.length) {
      $("myFlightsList").innerHTML = `<div class="muted">Пока пусто. Как твоя душа.</div>`;
      return;
    }
    for (const t of list) {
      const div = document.createElement("div");
      div.className = "item";
      div.innerHTML = `
        <div class="itemTop">
          <div class="big">${t.flight_number}</div>
          <div class="price">${t.price_usd} USD</div>
        </div>
        <div class="muted">${t.from} → ${t.to}</div>
        <div class="muted">${t.date} ${t.time}</div>
        <div class="muted">Место: <b>${t.seat_no}</b> • Статус: <b>${t.status}</b></div>
      `;
      $("myFlightsList").appendChild(div);
    }
  } catch (e) {
    err($("myFlightsErr"), e.message);
  }
}

// ---------------------------
// boot
// ---------------------------
window.addEventListener("load", () => {
  // wire buttons
  $("sendLoginCode").onclick = () => startCode("login");
  $("sendRegisterCode").onclick = () => startCode("register");
  $("verifyLogin").onclick = doLogin;
  $("finishRegister").onclick = finishRegister;

  $("searchBtn").onclick = doSearch;

  $("backToSearch").onclick = () => setScreen("search");
  $("refreshSeats").onclick = refreshSeats;
  $("sendBookingCode").onclick = sendBookingCode;
  $("confirmBooking").onclick = confirmBooking;

  $("myFlightsBtn").onclick = showMyFlights;
  $("backFromMyFlights").onclick = () => setScreen("search");

  $("logoutBtn").onclick = () => {
    currentUser = "";
    localStorage.removeItem("air_user");
    setScreen("auth");
  };

  // init screen
  if (currentUser) {
    $("username").value = currentUser;
    setScreen("search");
  } else {
    setScreen("auth");
  }
});
