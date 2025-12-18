(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  const LS_LANG = "airline_lang";
  const LS_API  = "airline_api_base";

  const DEBUG = new URLSearchParams(location.search).get("debug") === "1";

  const I18N = {
    ru: {
      title: "Авиакомпания: регистрация и бронирование",
      server: "Сервер",
      step1: "1. Подтверждение через Telegram",
      step2: "2. Регистрация пассажира",
      tgUserLabel: "Telegram @username",
      tgUserHint: "Например: @Kain_cr",
      getCode: "Получить код",
      lastName: "Фамилия",
      firstName: "Имя",
      middleName: "Отчество",
      passportId: "Серия и номер паспорта",
      passportHint: "Формат: AA0000000",
      birthDate: "Дата рождения",
      birthHint: "Выбери дату в календаре",
      phone: "Телефон",
      email: "E-mail",
      tgCodeReg: "Код из Telegram (регистрация)",
      finishReg: "Завершить регистрацию",
      footerHint: "Если ngrok меняется — нажми “Сервер” и вставь новый URL.",
      apiExplain: "Вставь ngrok URL сервера (без /docs). Я сохраню и больше не буду зудеть.",
      apiTip: "Можно ещё открыть так: ...?api=https://xxxx.ngrok-free.dev",
      cancel: "Отмена",
      save: "Сохранить",

      toastSaved: "Сервер сохранён.",
      needServer: "Сначала укажи адрес сервера (ngrok).",
      badUrl: "Это не похоже на нормальный URL.",
      badUser: "Введи Telegram username. Формат: @username",
      codeSent: "Код отправлен в Telegram.",
      netFail: "Не удалось достучаться до сервера.",
      unknownErr: "Что-то пошло не так."
    },
    en: {
      title: "Airline: registration & booking",
      server: "Server",
      step1: "1. Telegram verification",
      step2: "2. Passenger registration",
      tgUserLabel: "Telegram @username",
      tgUserHint: "Example: @Kain_cr",
      getCode: "Get code",
      lastName: "Last name",
      firstName: "First name",
      middleName: "Middle name",
      passportId: "Passport ID",
      passportHint: "Format: AA0000000",
      birthDate: "Birth date",
      birthHint: "Pick date in calendar",
      phone: "Phone",
      email: "E-mail",
      tgCodeReg: "Telegram code (registration)",
      finishReg: "Finish registration",
      footerHint: "If ngrok changes — click “Server” and paste new URL.",
      apiExplain: "Paste ngrok backend URL (without /docs). I’ll save it.",
      apiTip: "You can also open: ...?api=https://xxxx.ngrok-free.dev",
      cancel: "Cancel",
      save: "Save",

      toastSaved: "Server saved.",
      needServer: "Set backend URL first (ngrok).",
      badUrl: "That URL looks wrong.",
      badUser: "Enter Telegram username like @username",
      codeSent: "Code sent to Telegram.",
      netFail: "Cannot reach the server.",
      unknownErr: "Something went wrong."
    }
  };

  function t(key) {
    const lang = getLang();
    return (I18N[lang] && I18N[lang][key]) || I18N.ru[key] || key;
  }

  function getLang() {
    const saved = (localStorage.getItem(LS_LANG) || "").toLowerCase();
    return saved === "en" ? "en" : "ru";
  }

  function setLang(lang) {
    localStorage.setItem(LS_LANG, lang);
    document.documentElement.lang = lang;

    document.querySelectorAll("[data-i18n]").forEach(el => {
      const k = el.getAttribute("data-i18n");
      if (k) el.innerHTML = t(k);
    });

    // button label = opposite language
    const langBtn = $("#langBtn");
    if (langBtn) langBtn.textContent = (lang === "ru") ? "EN" : "RU";
  }

  function normalizeBase(u) {
    if (!u) return "";
    let s = String(u).trim();

    // remove /docs or /docs/ if user пастит со swagger
    s = s.replace(/\/docs\/?$/i, "");
    // remove trailing slashes
    s = s.replace(/\/+$/g, "");

    // if no protocol, add https
    if (!/^https?:\/\//i.test(s)) s = "https://" + s;

    return s;
  }

  function getApiBaseFromQuery() {
    const p = new URLSearchParams(location.search);
    const api = p.get("api");
    return api ? normalizeBase(api) : "";
  }

  function getApiBase() {
    const q = getApiBaseFromQuery();
    if (q) {
      localStorage.setItem(LS_API, q);
      return q;
    }
    const saved = localStorage.getItem(LS_API) || "";
    return saved ? normalizeBase(saved) : "";
  }

  let API_BASE = "";

  // UI helpers
  function setMsg(el, type, text) {
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.classList.remove("ok", "err");
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.classList.remove("ok", "err");
    el.classList.add(type === "ok" ? "ok" : "err");
    el.textContent = text;
  }

  let toastTimer = null;
  function toast(text) {
    const box = $("#toast");
    const txt = $("#toastText");
    if (!box || !txt) return;
    txt.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 2200);
  }

  // Modal (backend url)
  function openApiModal(prefill = "") {
    const back = $("#apiModal");
    const inp = $("#apiInput");
    if (!back || !inp) return;

    inp.value = prefill || API_BASE || "";
    back.hidden = false;

    // focus
    setTimeout(() => inp.focus(), 30);
  }

  function closeApiModal() {
    const back = $("#apiModal");
    if (back) back.hidden = true;
  }

  function isValidUrl(u) {
    try {
      const x = new URL(u);
      return x.protocol === "http:" || x.protocol === "https:";
    } catch {
      return false;
    }
  }

  // Network
  async function apiFetch(path, opts = {}) {
    if (!API_BASE) throw new Error("NO_API_BASE");

    const url = API_BASE + path;

    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 12000);

    try {
      const res = await fetch(url, {
        ...opts,
        mode: "cors",
        signal: ctrl.signal,
        headers: {
          "Content-Type": "application/json",
          ...(opts.headers || {})
        }
      });

      const raw = await res.text();
      let data = null;
      try { data = raw ? JSON.parse(raw) : null; } catch { /* ignore */ }

      if (!res.ok) {
        const err = new Error("HTTP_" + res.status);
        err.status = res.status;
        err.data = data;
        err.raw = raw;
        throw err;
      }

      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function prettyValidation(err) {
    // FastAPI often returns {detail:[{loc:..., msg:...}, ...]}
    const d = err && err.data;
    if (d && Array.isArray(d.detail) && d.detail.length) {
      // собрать человечески, без слива всей структуры
      const parts = d.detail
        .map(x => x && x.msg ? String(x.msg) : "")
        .filter(Boolean);
      if (parts.length) return parts[0];
    }
    if (d && typeof d.detail === "string") return d.detail;
    if (d && typeof d.message === "string") return d.message;
    return "";
  }

  // Stars (many)
  function initStars() {
    const c = $("#stars");
    if (!c) return;
    const ctx = c.getContext("2d", { alpha: true });
    if (!ctx) return;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      c.width = Math.floor(window.innerWidth * dpr);
      c.height = Math.floor(window.innerHeight * dpr);
      c.style.width = window.innerWidth + "px";
      c.style.height = window.innerHeight + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    }

    function draw() {
      const w = window.innerWidth;
      const h = window.innerHeight;
      ctx.clearRect(0, 0, w, h);

      // density: много звёзд, но без убийства FPS
      const count = Math.max(350, Math.min(2600, Math.floor((w * h) / 1800)));

      for (let i = 0; i < count; i++) {
        const x = Math.random() * w;
        const y = Math.random() * h;
        const r = Math.random() < 0.92 ? (Math.random() * 1.2 + 0.2) : (Math.random() * 1.8 + 0.8);
        const a = Math.random() * 0.55 + 0.18;

        // лёгкий пурпурный оттенок части звёзд
        const tint = Math.random();
        let col = `rgba(255,255,255,${a})`;
        if (tint < 0.18) col = `rgba(214,190,255,${a})`;
        else if (tint < 0.26) col = `rgba(255,190,244,${a})`;

        ctx.beginPath();
        ctx.fillStyle = col;
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    window.addEventListener("resize", resize, { passive: true });
    resize();
  }

  // App actions
  async function sendCode() {
    const msg = $("#authMsg");
    setMsg(msg, "ok", "");
    const inp = $("#tgUsername");
    const usernameRaw = (inp ? inp.value : "").trim();

    if (!usernameRaw) {
      setMsg(msg, "err", t("badUser"));
      return;
    }

    let username = usernameRaw;
    if (!username.startsWith("@")) username = "@" + username;
    if (!/^@[a-zA-Z0-9_]{4,64}$/.test(username)) {
      setMsg(msg, "err", t("badUser"));
      return;
    }

    try {
      const payload = {
        telegram_username: username,
        purpose: "registration"
      };

      await apiFetch("/api/auth/start", {
        method: "POST",
        body: JSON.stringify(payload)
      });

      setMsg(msg, "ok", t("codeSent"));
      toast(t("codeSent"));
    } catch (e) {
      if (DEBUG) console.error(e);

      if (String(e.message) === "NO_API_BASE") {
        setMsg(msg, "err", t("needServer"));
        openApiModal();
        return;
      }

      if (e.name === "AbortError") {
        setMsg(msg, "err", t("netFail"));
        return;
      }

      const v = prettyValidation(e);
      if (e.status === 422 && v) {
        setMsg(msg, "err", v);
        return;
      }

      setMsg(msg, "err", t("netFail"));
    }
  }

  // Регистрацию оставила “тихо”: без слива ошибок на экран.
  // Эндпоинт у тебя может отличаться — но UI не будет показывать внутренности сервера.
  async function registerPassenger() {
    const msg = $("#regMsg");
    setMsg(msg, "ok", "");

    const data = {
      telegram_username: (($("#tgUsername")?.value || "").trim().startsWith("@") ? ($("#tgUsername")?.value || "").trim() : "@" + (($("#tgUsername")?.value || "").trim())),
      last_name: ($("#lastName")?.value || "").trim(),
      first_name: ($("#firstName")?.value || "").trim(),
      middle_name: ($("#middleName")?.value || "").trim(),
      passport_id: ($("#passportId")?.value || "").trim(),
      birth_date: ($("#birthDate")?.value || "").trim(),
      phone: ($("#phone")?.value || "").trim(),
      email: ($("#email")?.value || "").trim(),
      telegram_code: ($("#tgCodeReg")?.value || "").trim()
    };

    // минимальные проверки
    if (!data.last_name || !data.first_name || !data.passport_id || !data.birth_date || !data.telegram_code) {
      setMsg(msg, "err", t("unknownErr"));
      return;
    }

    try {
      // попробуем самый вероятный путь
      await apiFetch("/api/passengers/register", {
        method: "POST",
        body: JSON.stringify(data)
      });

      setMsg(msg, "ok", "OK");
      toast("OK");
    } catch (e) {
      if (DEBUG) console.error(e);

      if (String(e.message) === "NO_API_BASE") {
        setMsg(msg, "err", t("needServer"));
        openApiModal();
        return;
      }

      const v = prettyValidation(e);
      if (e.status === 404) {
        setMsg(msg, "err", "На сервере нет /api/passengers/register (путь отличается).");
        return;
      }
      if (e.status === 422 && v) {
        setMsg(msg, "err", v);
        return;
      }

      setMsg(msg, "err", t("netFail"));
    }
  }

  function wireUi() {
    // language
    $("#langBtn")?.addEventListener("click", () => {
      const next = getLang() === "ru" ? "en" : "ru";
      setLang(next);
    });

    // server open
    $("#serverBtn")?.addEventListener("click", () => openApiModal());

    // modal close controls
    $("#apiClose")?.addEventListener("click", closeApiModal);
    $("#apiCancel")?.addEventListener("click", closeApiModal);

    // click outside = close
    $("#apiModal")?.addEventListener("mousedown", (e) => {
      if (e.target && e.target.id === "apiModal") closeApiModal();
    });

    // esc = close
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeApiModal();
    });

    // save api
    $("#apiSave")?.addEventListener("click", () => {
      const inp = $("#apiInput");
      const raw = (inp ? inp.value : "").trim();
      const base = normalizeBase(raw);

      if (!base || !isValidUrl(base)) {
        toast(t("badUrl"));
        return;
      }

      API_BASE = base;
      localStorage.setItem(LS_API, base);
      closeApiModal();
      toast(t("toastSaved"));
    });

    // enter in api input = save
    $("#apiInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") $("#apiSave")?.click();
    });

    // actions
    $("#btnGetCode")?.addEventListener("click", sendCode);
    $("#btnRegister")?.addEventListener("click", registerPassenger);

    // Telegram WebApp (не ломаемся, если его нет)
    try {
      const tg = window.Telegram && window.Telegram.WebApp;
      if (tg) {
        tg.ready();
        tg.expand();
      }
    } catch { /* ignore */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setLang(getLang());
    initStars();

    API_BASE = getApiBase();

    wireUi();

    // показываем модалку только если реально нет сервера
    if (!API_BASE) openApiModal();
  });
})();
