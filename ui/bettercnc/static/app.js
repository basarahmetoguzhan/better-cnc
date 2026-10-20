/* Thin client. All rendering happens on the server; this file only
   forwards intents and swaps in the returned HTML.
   Deliberately minimal: keeping the view logic in Python is what makes the
   safety rules testable without a browser. */

(function () {
  "use strict";

  var app = document.getElementById("app");
  var screen = (window.__VM__ && window.__VM__.screen) || "kontrol";
  // Kiosk mode only (app.py --boot-url): where to go once the UI is gone.
  var bootUrl = (window.__VM__ && window.__VM__.boot_url) || "";

  // ---- connection loss ----------------------------------------------------
  // The screen must never look live when it is not. A failed request used to
  // be swallowed and a hung server never failed at all, so the page froze on
  // its last values (found in sim/ on 2026-10-20). Now:
  //  - every request has a time limit, and only one poll is in flight;
  //  - LOST_MS after the last good answer the screen is covered;
  //  - a failed action covers it at once (its command may or may not have
  //    gone out);
  //  - in kiosk mode (app.py --boot-url) BOOT_MS after the last good answer
  //    the page goes back to the boot screen, which says why and returns here
  //    - once the boot screen answers: going to one that is down would put
  //    Chromium's own "can't connect" page on the screen.
  // The first good answer uncovers it. Why these values: deploy/README.md,
  // "Süreler".
  var POLL_EVERY_MS = 1000;      // the 1 Hz refresh
  var POLL_TIMEOUT_MS = 2500;    // ~2x the slowest normal answer (~1.2 s, an action holding the lock)
  var ACTION_TIMEOUT_MS = 4000;  // an action's own bounded waits, ~1.2 s, with room
  var LOST_MS = 3000;            // longer than any normal gap (~2.2 s), short enough to notice
  var BOOT_MS = 10000;           // a LinuxCNC + UI restart usually comes back before this
  var lastOk = Date.now();
  var inFlight = false;
  var bootAsking = false;
  var overlay = null;

  function line(cls, text) {
    var d = document.createElement("div");
    d.className = cls;
    d.textContent = text;
    return d;
  }

  function showLost(text) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "lost";
      overlay.setAttribute("role", "alert");
      document.body.appendChild(overlay);
    }
    overlay.textContent = "";
    overlay.appendChild(line("lost-title", "BAĞLANTI YOK"));
    overlay.appendChild(line("lost-text", text));
    overlay.appendChild(line("lost-hint",
      "Ekrandaki değerler ESKİ. Acil durumda makinenin gücünü kesin."));
  }

  function connectionOk() {
    lastOk = Date.now();
    if (overlay) {
      overlay.remove();
      overlay = null;
    }
  }

  function watchConnection() {
    var gap = Date.now() - lastOk;
    if (gap >= LOST_MS && !overlay) {
      showLost("Ekran, makinenin kontrol programından cevap alamıyor — yeniden deneniyor.");
    }
    if (bootUrl && gap >= BOOT_MS) goBoot();
  }

  // To the boot screen, if it answers. A cross-origin no-cors fetch: its
  // answer cannot be read, but it fails when nothing answers.
  function goBoot() {
    if (bootAsking) return;
    bootAsking = true;
    timed(new URL("healthz", bootUrl).href, { mode: "no-cors", cache: "no-store" },
          POLL_TIMEOUT_MS)
      .then(function () { location.replace(bootUrl); })
      .catch(function () { /* still covered; asked again on the next check */ })
      .then(function () { bootAsking = false; });
  }

  // fetch() with a time limit: a hung server must count as no answer.
  function timed(url, opts, ms) {
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, ms);
    opts = opts || {};
    opts.signal = ctl.signal;
    return fetch(url, opts).then(
      function (r) { clearTimeout(timer); return r; },
      function (e) { clearTimeout(timer); throw e; }
    );
  }

  function json(r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function post(action, value) {
    return timed("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, value: value, screen: screen })
    }, ACTION_TIMEOUT_MS)
      .then(json)
      .then(function (data) { connectionOk(); swap(data); })
      .catch(function () {
        showLost("Komut gönderilemedi: kontrol programı cevap vermedi. " +
                 "Komutun makineye ulaşıp ulaşmadığı bilinmiyor.");
      });
  }

  function swap(data) {
    if (!data || !data.html) return;
    var focusName = document.activeElement && document.activeElement.name;
    var caret = null;
    if (document.activeElement && document.activeElement.selectionStart != null) {
      caret = document.activeElement.selectionStart;
    }
    var value = document.activeElement && document.activeElement.value;

    app.innerHTML = data.html;
    if (bootUrl) noTooltips(app);

    if (focusName) {
      var again = app.querySelector('[name="' + focusName + '"]');
      if (again) {
        // preventScroll: a plain focus() scrolls the field into view, so with
        // the MDI field focused every 1 Hz poll dragged the page back to it -
        // the operator could not scroll up to the estop button (found in
        // sim/ on 2026-10-19).
        again.focus({ preventScroll: true });
        if (value != null) again.value = value;
        if (caret != null && again.setSelectionRange) {
          try { again.setSelectionRange(caret, caret); } catch (e) {}
        }
      }
    }
  }

  function refresh() {
    if (inFlight) return;  // one poll at a time: never pile up on a slow server
    inFlight = true;
    timed("/api/render?screen=" + encodeURIComponent(screen), null, POLL_TIMEOUT_MS)
      .then(json)
      .then(function (data) { connectionOk(); swap(data); })
      .catch(function () { /* watchConnection() covers the screen */ })
      .then(function () { inFlight = false; });
  }

  document.addEventListener("click", function (ev) {
    // Buttons only. The MDI <form> carries data-action="mdi" for the submit
    // handler below; matching any [data-action] made every click into the MDI
    // field post an empty MDI line, which logged "(boş)" and overwrote the
    // message line (found in sim/ on 2026-10-19).
    var btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    var action = btn.getAttribute("data-action");
    var value = btn.getAttribute("data-value") || "";

    if (action === "screen") {
      screen = value;
      ev.preventDefault();
      refresh();
      return;
    }
    if (action === "mdi_submit") {
      // handled by the form submit
      return;
    }
    ev.preventDefault();
    post(action, value);
  });

  document.addEventListener("submit", function (ev) {
    var form = ev.target.closest('[data-action="mdi"]');
    if (!form) return;
    ev.preventDefault();
    var input = form.querySelector('input[name="mdi"]');
    if (!input || !input.value.trim()) return;
    var cmd = input.value;
    input.value = "";
    post("mdi", cmd);
  });

  // Keep the MDI field usable: Enter submits, and history is server-side.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter") return;
    var input = ev.target.closest(".mdi-input");
    if (!input) return;
    var form = input.closest("form");
    if (form) {
      ev.preventDefault();
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  });

  // Kiosk: the browser's own tooltip box gives it away. Every reason a
  // tooltip carried is also on screen (banners, the feed note, the gate),
  // and the header's HAL names are for maintenance, on the Makine screen.
  function noTooltips(root) {
    var tipped = root.querySelectorAll("[title]");
    for (var i = 0; i < tipped.length; i++) tipped[i].removeAttribute("title");
  }

  // ---- kiosk: nothing of the browser shows -----------------------------------
  // Kiosk mode only (--boot-url), so a developer keeps F12 and the context
  // menu. The kiosk keymap (deploy/files/xkb) already stops Ctrl, Alt, Super
  // and the F-keys at the keyboard; this is the page's own layer, for a
  // keyboard or mouse the keymap does not cover: no context menu, no drag,
  // no selection outside the MDI field, no zoom, no back/forward buttons,
  // and every shortcut a page is allowed to stop (Chromium keeps Ctrl+T,
  // Ctrl+N, Ctrl+W for itself - the keymap is what stops those).
  if (bootUrl) {
    document.body.classList.add("kiosk");
    noTooltips(document);
    var stop = function (ev) { ev.preventDefault(); };
    document.addEventListener("contextmenu", stop);
    document.addEventListener("dragstart", stop);
    document.addEventListener("auxclick", stop);
    document.addEventListener("mouseup", function (ev) {
      if (ev.button === 3 || ev.button === 4) ev.preventDefault();
    });
    document.addEventListener("wheel", function (ev) {
      if (ev.ctrlKey) ev.preventDefault();
    }, { passive: false });
    document.addEventListener("keydown", function (ev) {
      var altgr = ev.getModifierState && ev.getModifierState("AltGraph");
      if (ev.ctrlKey || ev.metaKey || (ev.altKey && !altgr) || /^F\d{1,2}$/.test(ev.key) ||
          ev.key === "Tab") {
        ev.preventDefault();
        ev.stopPropagation();
      }
    }, true);
  }

  setInterval(refresh, POLL_EVERY_MS);
  setInterval(watchConnection, 500);
})();
