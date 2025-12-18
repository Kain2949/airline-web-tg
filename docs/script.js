(() => {
  const API_BASE =
    (location.hostname === "localhost" || location.hostname === "127.0.0.1")
      ? "http://localhost:8000"
      : "https://kristan-labored-earsplittingly.ngrok-free.dev";

  const $ = (id) => document.getElementById(id);

  const I18N = {
    ru: {
      title: "Регистрация на рейс",
      tgTitle: "Telegram подтверждение",
      tgUser: "Telegram @username",
      tgHint: "Сначала открой бота и нажми /start",
      getCode: "Получить код",
      regTitle: "Регистрация пассажира",
      ln: "Фамилия",
      fn: "Имя",
      mn: "Отчество",
      pp: "Серия и номер",
      ppHint: "подсказка: AA0000000",
      bd: "Дата рождения",
      ph: "Телефон",
      em: "E-mail",
      code: "Код из Telegram",
      finish: "Завершить",

      needUser: "Введи @username нормально.",
      needStart: "Сначала зайди в бота и нажми /start, иначе он тебе не напишет.",
      sent: "Код отправлен в Telegram.",
      wrongCode: "Код неверный или просрочен.",
      okReg: "Регистрация отправлена. Бот пришлёт подтверждение.",
      netFail: "Сервер недоступен (ngrok/бот не запущен).",
      badFields: "Проверь поля (дата рождения, паспорт, код)."
    },
    en: {
      title: "Flight registration",
      tgTitle: "Telegram verification",
      tgUser: "Telegram @username",
      tgHint: "Open the bot and press /start first",
      getCode: "Get code",
      regTitle: "Passenger registration",
      ln: "Last name",
      fn: "First name",
      mn: "Middle name",
      pp: "Passport ID",
      ppHint: "hint: AA0000000",
      bd: "Birth date",
      ph: "Phone",
      em: "E-mail",
      code: "Telegram code",
      finish: "Finish",

      needUser: "Enter a valid @username.",
      needStart: "Open the bot and press /start first.",
      sent: "Code was sent to Telegram.",
      wrongCode: "Invalid or expired code.",
      okReg: "Registration sent. Bot will confirm.",
      netFail: "Server is unreachable (ngrok/bot not running).",
      badFields: "Check fields (birth date, passport, code)."
    }
  };

  let lang = (localStorage.getItem("lang") || "ru").toLowerCase();
  if (!I18N[lang]) lang = "ru";

  function t(k) { return I18N[lang][k] || k; }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(n => {
      const key = n.getAttribute("data-i18n");
      if (key) n.textContent = t(key);
    });
    $("langBtn").textContent = (lang === "ru") ? "EN" : "RU";
  }

  function setMsg(node, text, ok) {
    node.hidden = !text;
    node.textContent = text || "";
    node.classList.remove("ok", "bad");
    if (text) node.classList.add(ok ? "ok" : "bad");
  }

  function normUser(u) {
    let s = (u || "").trim();
    if (!s) return "";
    if (!s.startsWith("@")) s = "@" + s;
    return s;
  }

  async function post(path, body) {
    const r = await fetch(API_BASE + path, {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });

    let data = null;
    try { data = await r.json(); } catch { /* ignore */ }

    if (!r.ok) {
      const e = new Error("HTTP_" + r.status);
      e.status = r.status;
      e.data = data;
      throw e;
    }
    return data;
  }

  // Stars (много)
  function initStars() {
    const c = $("stars");
    const ctx = c.getContext("2d");

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      c.width = Math.floor(innerWidth * dpr);
      c.height = Math.floor(innerHeight * dpr);
      c.style.width = innerWidth + "px";
      c.style.height = innerHeight + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    }

    function draw() {
      const w = innerWidth, h = innerHeight;
      ctx.clearRect(0, 0, w, h);

      const count = Math.max(500, Math.min(3500, Math.floor((w * h) / 1400)));
      for (let i = 0; i < count; i++) {
        const x = Math.random() * w;
        const y = Math.random() * h;
        const r = Math.random() * 1.2 + 0.2;
        const a = Math.random() * 0.6 + 0.15;

        const tint = Math.random();
        let col = `rgba(255,255,255,${a})`;
        if (tint < 0.14) col = `rgba(214,190,255,${a})`;
        else if (tint < 0.22) col = `rgba(255,190,244,${a})`;

        ctx.beginPath();
        ctx.fillStyle = col;
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    addEventListener("resize", resize, { passive: true });
    resize();
  }

  async function onGetCode() {
    const m1 = $("m1");
    setMsg(m1, "", true);

    const u = normUser($("tgUser").value);
    if (!u) return setMsg(m1, t("needUser"), false);

    $("btnCode").disabled = true;
    try {
      await post("/api/auth/start", { telegram_username: u, purpose: "register" });
      setMsg(m1, t("sent"), true);
    } catch (e) {
      if (e.status === 409) return setMsg(m1, t("needStart"), false);
      if (e.status === 422) return setMsg(m1, t("badFields"), false);
      setMsg(m1, t("netFail"), false);
    } finally {
      $("btnCode").disabled = false;
    }
  }

  async function onRegister() {
    const m2 = $("m2");
    setMsg(m2, "", true);

    const u = normUser($("tgUser").value);
    const code = ($("code").value || "").trim();

    const payload = {
      telegram_username: u,
      code: code,
      last_name: ($("ln").value || "").trim(),
      first_name: ($("fn").value || "").trim(),
      middle_name: ($("mn").value || "").trim() || null,
      passport_no: ($("pp").value || "").trim(),
      birth_date: ($("bd").value || "").trim(),
      phone: ($("ph").value || "").trim(),
      email: ($("em").value || "").trim()
    };

    if (!payload.telegram_username) return setMsg(m2, t("needUser"), false);

    $("btnReg").disabled = true;
    try {
      await post("/api/passengers/register", payload);
      setMsg(m2, t("okReg"), true);
    } catch (e) {
      if (e.status === 409) return setMsg(m2, t("needStart"), false);
      if (e.status === 400) return setMsg(m2, t("wrongCode"), false);
      if (e.status === 422) return setMsg(m2, t("badFields"), false);
      setMsg(m2, t("netFail"), false);
    } finally {
      $("btnReg").disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initStars();
    applyI18n();

    $("langBtn").addEventListener("click", () => {
      lang = (lang === "ru") ? "en" : "ru";
      localStorage.setItem("lang", lang);
      applyI18n();
    });

    $("btnCode").addEventListener("click", onGetCode);
    $("btnReg").addEventListener("click", onRegister);
  });
})();
