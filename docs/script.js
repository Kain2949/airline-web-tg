(() => {
  // ==============================
  // API BASE
  // ==============================
  // Удобно: один раз открываешь сайт так:
  // https://kain2949.github.io/airline-web-tg/?api=https://xxxx.ngrok-free.dev
  // и оно сохранится в localStorage.
  const API_BASE = (() => {
    const qp = new URLSearchParams(location.search);
    const fromQ = (qp.get("api") || "").trim();
    if (fromQ) {
      const clean = normalizeBase(fromQ);
      localStorage.setItem("api_base", clean);
      return clean;
    }
    const saved = (localStorage.getItem("api_base") || "").trim();
    if (saved) return normalizeBase(saved);

    // запасной вариант — ПОДСТАВЬ СВОЙ NGROK, если не хочешь через ?api=
    return "https://kristan-labored-earsplittingly.ngrok-free.dev";
  })();

  // ==============================
  // i18n
  // ==============================
  const I18N = {
    ru: {
      title: "Авиакомпания: регистрация и бронирование",
      tgTitle: "1. Подтверждение через Telegram",
      tgLabel: "Telegram @username",
      tgBtn: "Получить код",
      regTitle: "2. Регистрация пассажира",
      lnLabel: "Фамилия",
      fnLabel: "Имя",
      mnLabel: "Отчество",
      pidLabel: "Серия и номер паспорта",
      bdLabel: "Дата рождения",
      phLabel: "Телефон",
      emLabel: "E-mail",
      codeLabel: "Код из Telegram (регистрация)",
      regBtn: "Завершить регистрацию",
      s_api: (v) => `API: ${v}`,
      s_need_user: "Впиши Telegram @username.",
      s_sent: "Код отправлен в Telegram. Проверь чат с ботом.",
      s_need_code: "Впиши код из Telegram.",
      s_bad_user: "Нужен @username (можно без @, я сама добавлю).",
      s_reg_ok: "Регистрация успешна. Теперь можешь бронировать.",
    },
    en: {
      title: "Airline: registration & booking",
      tgTitle: "1. Telegram verification",
      tgLabel: "Telegram @username",
      tgBtn: "Get code",
      regTitle: "2. Passenger registration",
      lnLabel: "Last name",
      fnLabel: "First name",
      mnLabel: "Middle name",
      pidLabel: "Passport ID",
      bdLabel: "Birth date",
      phLabel: "Phone",
      emLabel: "E-mail",
      codeLabel: "Telegram code (registration)",
      regBtn: "Complete registration",
      s_api: (v) => `API: ${v}`,
      s_need_user: "Enter Telegram @username.",
      s_sent: "Code sent to Telegram. Check the bot chat.",
      s_need_code: "Enter the Telegram code.",
      s_bad_user: "Need @username (you can omit @ — I'll add it).",
      s_reg_ok: "Registration completed. You can book now.",
    },
  };

  // ==============================
  // DOM helpers
  // ==============================
  const $ = (id) => document.getElementById(id);

  const el = {
    langBtn: null,

    tgUsername: null,
    btnGetCode: null,
    tgStatus: null,

    lastName: null,
    firstName: null,
    middleName: null,
    passportId: null,
    birthDate: null,
    phone: null,
    email: null,
    regCode: null,
    btnRegister: null,
    regStatus: null,

    globalStatus: null,
  };

  let lang = (localStorage.getItem("lang") || "ru").toLowerCase();
  if (!I18N[lang]) lang = "ru";

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    // подцепляем элементы (и НЕ падаем, если что-то не так)
    el.langBtn = $("langBtn");
    el.tgUsername = $("tgUsername");
    el.btnGetCode = $("btnGetCode");
    el.tgStatus = $("tgStatus");

    el.lastName = $("lastName");
    el.firstName = $("firstName");
    el.middleName = $("middleName");
    el.passportId = $("passportId");
    el.birthDate = $("birthDate");
    el.phone = $("phone");
    el.email = $("email");
    el.regCode = $("regCode");
    el.btnRegister = $("btnRegister");
    el.regStatus = $("regStatus");

    el.globalStatus = $("globalStatus");

    // если что-то ключевое отсутствует — покажем понятно, а не "null.addEventListener"
    const must = [
      ["langBtn", el.langBtn],
      ["tgUsername", el.tgUsername],
      ["btnGetCode", el.btnGetCode],
      ["btnRegister", el.btnRegister],
    ];
    const missing = must.filter(([, v]) => !v).map(([k]) => k);
    if (missing.length) {
      console.error("DOM missing:", missing);
      setGlobal(`DOM missing: ${missing.join(", ")} (index.html != script.js)`, "err");
      return;
    }

    // язык
    el.langBtn.addEventListener("click", () => {
      lang = lang === "ru" ? "en" : "ru";
      localStorage.setItem("lang", lang);
      applyI18n();
    });

    // кнопки
    el.btnGetCode.addEventListener("click", onGetCode);
    el.btnRegister.addEventListener("click", onRegister);

    // первичная отрисовка
    applyI18n();
    setGlobal(I18N[lang].s_api(API_BASE), "ok");
  }

  function applyI18n() {
    const dict = I18N[lang];
    document.documentElement.lang = lang;

    // поменять тексты
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      const key = node.getAttribute("data-i18n");
      const val = dict[key];
      if (typeof val === "string") node.textContent = val;
    });

    // кнопка языка
    if (el.langBtn) el.langBtn.textContent = lang === "ru" ? "EN" : "RU";
  }

  // ==============================
  // Actions
  // ==============================
  async function onGetCode() {
    clearStatus();

    const u = normalizeUsername(el.tgUsername.value);
    if (!u) return setTg(I18N[lang].s_need_user, "err");
    if (!looksLikeUsername(u)) return setTg(I18N[lang].s_bad_user, "err");

    disable(el.btnGetCode, true);

    try {
      const res = await apiPost("/api/auth/start", { username: u });
      if (!res.ok) {
        return setTg(formatApiError(res), "err");
      }
      setTg(I18N[lang].s_sent, "ok");
    } catch (e) {
      console.error(e);
      setTg(`Ошибка сети: ${String(e)}`, "err");
    } finally {
      disable(el.btnGetCode, false);
    }
  }

  async function onRegister() {
    clearStatus();

    const u = normalizeUsername(el.tgUsername.value);
    if (!u || !looksLikeUsername(u)) return setReg(I18N[lang].s_need_user, "err");

    const code = (el.regCode.value || "").trim();
    if (!code) return setReg(I18N[lang].s_need_code, "err");

    const payload = {
      username: u,
      code: code,

      last_name: (el.lastName.value || "").trim(),
      first_name: (el.firstName.value || "").trim(),
      middle_name: (el.middleName.value || "").trim() || null,

      passport_id: (el.passportId.value || "").trim(),
      birth_date: (el.birthDate.value || "").trim(), // yyyy-mm-dd
      phone: (el.phone.value || "").trim(),
      email: (el.email.value || "").trim(),
    };

    // минимальная валидация на фронте (не “душу”, но глупости режу)
    const miss = [];
    if (!payload.last_name) miss.push("last name");
    if (!payload.first_name) miss.push("first name");
    if (!payload.passport_id) miss.push("passport");
    if (!payload.birth_date) miss.push("birth date");
    if (!payload.phone) miss.push("phone");
    if (!payload.email) miss.push("email");
    if (miss.length) return setReg(`Заполни поля: ${miss.join(", ")}`, "err");

    disable(el.btnRegister, true);

    try {
      // ВАЖНО: если у тебя в backend другой путь — меняешь только ЭТУ строку.
      const res = await apiPost("/api/passengers/register", payload);

      if (!res.ok) {
        // если вдруг у тебя эндпоинт называется иначе, сразу увидишь 404 здесь.
        return setReg(formatApiError(res), "err");
      }

      setReg(I18N[lang].s_reg_ok, "ok");
    } catch (e) {
      console.error(e);
      setReg(`Ошибка сети: ${String(e)}`, "err");
    } finally {
      disable(el.btnRegister, false);
    }
  }

  // ==============================
  // API helpers
  // ==============================
  async function apiPost(path, body) {
    const url = API_BASE + path;
    const r = await fetch(url, {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await safeRead(r);
    return { ok: r.ok, status: r.status, data, url };
  }

  async function safeRead(resp) {
    const ct = (resp.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("application/json")) {
      try { return await resp.json(); } catch { return null; }
    }
    try { return await resp.text(); } catch { return null; }
  }

  function formatApiError(res) {
    const d = res.data;
    if (typeof d === "string" && d.trim()) return `HTTP ${res.status}: ${d}`;
    if (d && typeof d === "object") {
      // fastapi часто отдаёт {detail: "..."} или массив ошибок
      if (d.detail) return `HTTP ${res.status}: ${stringifyDetail(d.detail)}`;
      return `HTTP ${res.status}: ${JSON.stringify(d)}`;
    }
    return `HTTP ${res.status}: ошибка`;
  }

  function stringifyDetail(detail) {
    if (typeof detail === "string") return detail;
    try { return JSON.stringify(detail); } catch { return String(detail); }
  }

  // ==============================
  // Status UI
  // ==============================
  function clearStatus() {
    if (el.tgStatus) el.tgStatus.textContent = "";
    if (el.regStatus) el.regStatus.textContent = "";
    if (el.globalStatus) el.globalStatus.textContent = "";
  }

  function setTg(msg, kind) {
    if (!el.tgStatus) return;
    el.tgStatus.textContent = msg;
    el.tgStatus.dataset.kind = kind;
    setGlobal(msg, kind);
  }

  function setReg(msg, kind) {
    if (!el.regStatus) return;
    el.regStatus.textContent = msg;
    el.regStatus.dataset.kind = kind;
    setGlobal(msg, kind);
  }

  function setGlobal(msg, kind) {
    if (!el.globalStatus) return;
    el.globalStatus.textContent = msg;
    el.globalStatus.dataset.kind = kind;
  }

  function disable(btn, v) {
    if (!btn) return;
    btn.disabled = !!v;
    btn.setAttribute("aria-disabled", v ? "true" : "false");
  }

  // ==============================
  // Utils
  // ==============================
  function normalizeUsername(v) {
    let s = (v || "").trim();
    if (!s) return "";
    if (!s.startsWith("@")) s = "@" + s;
    return s;
  }

  function looksLikeUsername(u) {
    // @ + 5..32 символа, буквы/цифры/подчёркивания
    return /^@[a-zA-Z0-9_]{5,32}$/.test(u);
  }

  function normalizeBase(v) {
    let s = (v || "").trim();
    s = s.replace(/\/+$/, "");
    return s;
  }
})();
