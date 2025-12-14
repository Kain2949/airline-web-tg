(() => {
  "use strict";

  const el = (id) => document.getElementById(id);

  const toast = el("toast");
  const toastTitle = el("toastTitle");
  const toastText = el("toastText");
  const toastClose = el("toastClose");

  const apiModal = el("apiModal");
  const apiInput = el("apiInput");
  const apiCancel = el("apiCancel");
  const apiSave = el("apiSave");

  const tgUsername = el("tgUsername");
  const btnAuthStart = el("btnAuthStart");

  const lastName = el("lastName");
  const firstName = el("firstName");
  const middleName = el("middleName");
  const passportId = el("passportId");
  const birthDate = el("birthDate");
  const phone = el("phone");
  const email = el("email");
  const regCode = el("regCode");
  const btnRegister = el("btnRegister");

  const langBtn = el("langBtn");

  // -------- API base (ngrok) without leaking it into the page -----------
  function normalizeBase(s) {
    return (s || "").trim().replace(/\/+$/, "");
  }

  function getApiBase() {
    const u = new URL(window.location.href);
    const qp = u.searchParams.get("api");
    if (qp) {
      const base = normalizeBase(qp);
      localStorage.setItem("api_base", base);
      return base;
    }
    const stored = localStorage.getItem("api_base");
    if (stored) return normalizeBase(stored);

    // local dev
    if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
      return "http://localhost:8000";
    }
    return null;
  }

  let API_BASE = getApiBase();

  function showApiModal() {
    apiModal.hidden = false;
    apiInput.value = "";
    apiInput.focus();
  }

  function hideApiModal() {
    apiModal.hidden = true;
  }

  apiCancel?.addEventListener("click", () => {
    hideApiModal();
    showToast("Ошибка", "Без адреса сервера ничего не заработает. Укажи ngrok URL.");
  });

  apiSave?.addEventListener("click", () => {
    const v = normalizeBase(apiInput.value);
    if (!v.startsWith("http")) {
      showToast("Ошибка", "Это не похоже на URL. Пример: https://xxxx.ngrok-free.dev");
      return;
    }
    localStorage.setItem("api_base", v);
    API_BASE = v;
    hideApiModal();
    showToast("Ок", "Backend URL сохранён.");
  });

  // -------------------- UI: toast -----------------------
  let toastTimer = null;
  function showToast(title, text) {
    toastTitle.textContent = title;
    toastText.textContent = text;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 4500);
  }

  toastClose?.addEventListener("click", () => (toast.hidden = true));

  // -------------------- i18n ----------------------------
  const dict = {
    ru: {
      title: "Авиакомпания: регистрация и бронирование",
      step1: "1. Подтверждение через Telegram",
      tgLabel: "Telegram @username",
      tgHint: "Например: @Kain_cr",
      getCode: "Получить код",
      step2: "2. Регистрация пассажира",
      last: "Фамилия",
      first: "Имя",
      middle: "Отчество",
      passport: "Серия и номер паспорта",
      passHint: "Формат: AA0000000",
      birth: "Дата рождения",
      birthHint: "Выбери дату в календаре",
      phone: "Телефон",
      email: "E-mail",
      regCode: "Код из Telegram (регистрация)",
      regHint: "6 цифр",
      finishReg: "Завершить регистрацию",
      msgSent: "Код отправлен в Telegram.",
      msgNoServer: "Сервер не настроен. Нужен ngrok URL.",
      msgBadUser: "Введи корректный @username.",
      msgFail: "Не удалось связаться с сервером.",
      msgOk: "Готово.",
    },
    en: {
      title: "Airline: registration & booking",
      step1: "1. Telegram verification",
      tgLabel: "Telegram @username",
      tgHint: "Example: @Kain_cr",
      getCode: "Get code",
      step2: "2. Passenger registration",
      last: "Last name",
      first: "First name",
      middle: "Middle name",
      passport: "Passport ID",
      passHint: "Format: AA0000000",
      birth: "Birth date",
      birthHint: "Pick a date in the calendar",
      phone: "Phone",
      email: "E-mail",
      regCode: "Telegram code (registration)",
      regHint: "6 digits",
      finishReg: "Finish registration",
      msgSent: "Code sent to Telegram.",
      msgNoServer: "Backend is not set. You need an ngrok URL.",
      msgBadUser: "Enter a valid @username.",
      msgFail: "Failed to reach the server.",
      msgOk: "Done.",
    },
  };

  function getLang() {
    return localStorage.getItem("lang") || "ru";
  }
  function setLang(v) {
    localStorage.setItem("lang", v);
  }
  function applyLang() {
    const L = dict[getLang()];
    el("t_title").textContent = L.title;
    el("t_step1").textContent = L.step1;
    el("t_tgLabel").textContent = L.tgLabel;
    el("t_tgHint").textContent = L.tgHint;
    el("t_getCode").textContent = L.getCode;

    el("t_step2").textContent = L.step2;
    el("t_last").textContent = L.last;
    el("t_first").textContent = L.first;
    el("t_middle").textContent = L.middle;
    el("t_passport").textContent = L.passport;
    el("t_passHint").textContent = L.passHint;
    el("t_birth").textContent = L.birth;
    el("t_birthHint").textContent = L.birthHint;
    el("t_phone").textContent = L.phone;
    el("t_email").textContent = L.email;
    el("t_regCode").textContent = L.regCode;
    el("t_regHint").textContent = L.regHint;
    el("t_finishReg").textContent = L.finishReg;

    langBtn.textContent = getLang() === "ru" ? "EN" : "RU";
  }

  langBtn?.addEventListener("click", () => {
    const next = getLang() === "ru" ? "en" : "ru";
    setLang(next);
    applyLang();
  });

  // -------------------- helpers -------------------------
  function cleanUsername(s) {
    s = (s || "").trim();
    if (!s) return "";
    if (!s.startsWith("@")) s = "@" + s;
    // very basic validation
    if (!/^@[A-Za-z0-9_]{5,32}$/.test(s)) return "";
    return s;
  }

  async function fetchJson(path, payload) {
    if (!API_BASE) throw new Error("NO_API_BASE");

    const url = API_BASE + path;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 12000);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        mode: "cors",
        signal: ctrl.signal,
      });

      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = null; }

      if (!res.ok) {
        const err = new Error("HTTP_" + res.status);
        err.status = res.status;
        err.data = data;
        // детали в консоль, не в UI
        console.error("API error:", res.status, data || text);
        throw err;
      }
      return data;
    } finally {
      clearTimeout(t);
    }
  }

  // -------------------- actions -------------------------
  btnAuthStart?.addEventListener("click", async () => {
    const L = dict[getLang()];

    if (!API_BASE) {
      showToast("Backend", L.msgNoServer);
      showApiModal();
      return;
    }

    const u = cleanUsername(tgUsername.value);
    if (!u) {
      showToast("Ошибка", L.msgBadUser);
      tgUsername.focus();
      return;
    }

    btnAuthStart.disabled = true;
    try {
      // IMPORTANT: FastAPI ждёт именно эти поля (судя по твоему 422)
      await fetchJson("/api/auth/start", {
        telegram_username: u,
        purpose: "registration",
      });
      showToast("Ок", L.msgSent);
    } catch (e) {
      if (e && e.message === "NO_API_BASE") {
        showToast("Backend", L.msgNoServer);
        showApiModal();
      } else if (e && e.status === 422) {
        showToast("Ошибка", "Сервер не принял данные. Проверь @username.");
      } else {
        showToast("Ошибка", L.msgFail);
      }
    } finally {
      btnAuthStart.disabled = false;
    }
  });

  btnRegister?.addEventListener("click", async () => {
    const L = dict[getLang()];
    if (!API_BASE) {
      showToast("Backend", L.msgNoServer);
      showApiModal();
      return;
    }

    const u = cleanUsername(tgUsername.value);
    if (!u) { showToast("Ошибка", L.msgBadUser); tgUsername.focus(); return; }

    const payload = {
      telegram_username: u,
      code: (regCode.value || "").trim(),
      last_name: (lastName.value || "").trim(),
      first_name: (firstName.value || "").trim(),
      middle_name: (middleName.value || "").trim() || null,
      passport_id: (passportId.value || "").trim(),
      birth_date: birthDate.value || null, // yyyy-mm-dd
      phone: (phone.value || "").trim(),
      email: (email.value || "").trim(),
    };

    // мягкая валидация без истерик
    if (!payload.last_name || !payload.first_name || !payload.passport_id || !payload.birth_date || !payload.phone || !payload.email || !payload.code) {
      showToast("Ошибка", "Заполни обязательные поля и введи код.");
      return;
    }

    btnRegister.disabled = true;
    try {
      // Если у тебя эндпоинт называется иначе — поменяй только ЭТУ строку.
      await fetchJson("/api/register", payload);
      showToast("Ок", L.msgOk);
    } catch (e) {
      if (e && e.status === 422) {
        showToast("Ошибка", "Сервер ругается на поля. Проверь формат данных.");
      } else {
        showToast("Ошибка", L.msgFail);
      }
    } finally {
      btnRegister.disabled = false;
    }
  });

  // init
  applyLang();

  // if backend isn't configured - ask once (no leaking)
  if (!API_BASE) {
    // не мешаем сразу, только если пользователь нажмёт кнопку
    console.warn("API_BASE is not set. Use ?api=... or set in modal.");
  }
})();
