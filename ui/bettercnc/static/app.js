/* Thin client. All rendering happens on the server; this file only
   forwards intents and swaps in the returned HTML.
   Deliberately minimal: keeping the view logic in Python is what makes the
   safety rules testable without a browser. */

(function () {
  "use strict";

  var app = document.getElementById("app");
  var screen = (window.__VM__ && window.__VM__.screen) || "kontrol";
  // The Program list's page (P0 cleanup): the browser's own view state, like
  // `screen` - sent with every render and action, never an action itself.
  var programPage = 0;
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
  // The stop rail and the held press (below):
  var INSTANT_GUARD_MS = 500;    // a click this soon after a rail button's pointerdown is the same press
  var SWAP_AFTER_UP_MS = 50;     // a re-render held for a press waits this long after the release
  var SWAP_HOLD_MAX_MS = 3000;   // ...and is never held longer than LOST_MS from the press
  var lastOk = Date.now();
  var inFlight = false;
  var bootAsking = false;
  var overlay = null;
  var lostText = "";       // what the overlay says
  var lostEstop = "";      // what became of an estop sent from it

  function line(cls, text) {
    var d = document.createElement("div");
    d.className = cls;
    d.textContent = text;
    return d;
  }

  // The overlay keeps an estop button (audit 2026-10-21: it used to cover
  // the one estop this rig has). The server runs software_estop without
  // taking its lock, so a server that is slow rather than dead still acts
  // on it; the page cannot tell which, and says so. It fires on the press,
  // like the rail's (data-instant, handled below).
  function showLost(text) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "lost";
      overlay.setAttribute("role", "alert");
      document.body.appendChild(overlay);
    }
    lostText = text;
    overlay.textContent = "";
    overlay.appendChild(line("lost-title", "BAĞLANTI YOK"));
    overlay.appendChild(line("lost-text", text));
    overlay.appendChild(line("lost-hint",
      "Ekrandaki değerler ESKİ. Acil durumda makinenin gücünü kesin."));
    var estop = document.createElement("button");
    estop.type = "button";
    estop.className = "lost-estop";
    estop.setAttribute("data-action", "software_estop");
    estop.setAttribute("data-instant", "1");
    estop.textContent = "YAZILIM ACİL STOP GÖNDER";
    overlay.appendChild(estop);
    if (lostEstop) overlay.appendChild(line("lost-sent", lostEstop));
  }

  function sendLostEstop() {
    lostEstop = "Acil stop gönderildi — makineye ULAŞTIĞI BİLİNMİYOR. Emin olmak için gücü kesin.";
    showLost(lostText);
    timed("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "software_estop", value: "", screen: screen })
    }, ACTION_TIMEOUT_MS)
      .then(json)
      .then(function (data) { lostEstop = ""; connectionOk(); swap(data); })
      .catch(function () {
        lostEstop = "Acil stop gönderildi, cevap GELMEDİ — makineye ulaştığı bilinmiyor. " +
                    "Emin olmak için gücü kesin.";
        if (overlay) showLost(lostText);
      });
  }

  function connectionOk() {
    lastOk = Date.now();
    lostEstop = "";
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

  // answered / failed: optional, for the stop rail's band (fireInstant).
  function post(action, value, answered, failed) {
    return timed("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, value: value, screen: screen, page: programPage })
    }, ACTION_TIMEOUT_MS)
      .then(json)
      .then(function (data) { if (answered) answered(); connectionOk(); swap(data); })
      .catch(function () {
        if (failed) failed();
        showLost("Komut gönderilemedi: kontrol programı cevap vermedi. " +
                 "Komutun makineye ulaşıp ulaşmadığı bilinmiyor.");
      });
  }

  // ---- presses survive the 1 Hz re-render ---------------------------------
  // A mouse press that spans a swap was lost: the button under the pointer
  // is a new node at mouseup, and Chrome then fires no click (headless
  // Chrome, 2026-11-13: 5 of 5 presses across a swap gave no click, 5 of 5
  // between swaps did; touch taps survived). So while a pointer pressed
  // inside #app is down, a swap waits - only the newest answer is kept -
  // until SWAP_AFTER_UP_MS after the release, and never longer than
  // SWAP_HOLD_MAX_MS from the press: a screen held longer would look live
  // while stale. A press on the rail or the overlay holds nothing: neither
  // is swapped, and the estop's answer should show at once.
  var down = {};            // pointerId -> true while pressed
  var downCount = 0;
  var downSince = 0;
  var releaseTimer = null;
  var pendingSwap = null;

  function pressHeld() {
    return (downCount > 0 || releaseTimer !== null) &&
           Date.now() - downSince < SWAP_HOLD_MAX_MS;
  }

  function pointerPressed(ev) {
    var now = Date.now();
    // A release we never heard of (outside the window, say) must not hold
    // the next press's swaps for good.
    if (now - downSince >= SWAP_HOLD_MAX_MS) { down = {}; downCount = 0; }
    if (!down[ev.pointerId]) { down[ev.pointerId] = true; downCount++; }
    if (downCount === 1) downSince = now;
  }

  function pointerReleased(ev) {
    if (down[ev.pointerId]) { delete down[ev.pointerId]; downCount--; }
    if (downCount > 0) return;
    clearTimeout(releaseTimer);
    releaseTimer = setTimeout(function () {
      releaseTimer = null;
      if (pendingSwap && !pressHeld()) {
        var data = pendingSwap;
        pendingSwap = null;
        applySwap(data);
      }
    }, SWAP_AFTER_UP_MS);
  }

  document.addEventListener("pointerup", pointerReleased, true);
  document.addEventListener("pointercancel", pointerReleased, true);

  function swap(data) {
    if (!data || !data.html) return;
    if (pressHeld()) {
      pendingSwap = data;
      return;
    }
    pendingSwap = null;
    applySwap(data);
  }

  function applySwap(data) {
    var focusName = document.activeElement && document.activeElement.name;
    var caret = null;
    if (document.activeElement && document.activeElement.selectionStart != null) {
      caret = document.activeElement.selectionStart;
    }
    var value = document.activeElement && document.activeElement.value;

    app.innerHTML = data.html;
    if (bootUrl) noTooltips(app);
    if (sentAnswered === sentToken) hideSent();
    scheduleReadyRender();

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

  // «Evet» on the confirmation band turns on CONFIRM_MIN_S after the question
  // (app.Session). The band says in how many ms (data-ready-in-ms): one extra
  // render then, so it turns on at that moment, not at the next 1 Hz poll
  // (1-2 s later). The server's rule stands: an early «Evet» is no answer.
  var READY_MARGIN_MS = 30;
  var readyTimer = null;

  function scheduleReadyRender() {
    var band = app.querySelector("[data-ready-in-ms]");
    clearTimeout(readyTimer);
    readyTimer = null;
    if (!band) return;
    var ms = parseInt(band.getAttribute("data-ready-in-ms"), 10);
    if (!(ms > 0)) return;
    readyTimer = setTimeout(function readyRender() {
      readyTimer = null;
      if (inFlight) { readyTimer = setTimeout(readyRender, 50); return; }
      refresh();
    }, ms + READY_MARGIN_MS);
  }

  function refresh() {
    if (inFlight) return;  // one poll at a time: never pile up on a slow server
    inFlight = true;
    timed("/api/render?screen=" + encodeURIComponent(screen) + "&page=" + programPage,
          null, POLL_TIMEOUT_MS)
      .then(json)
      .then(function (data) { connectionOk(); swap(data); })
      .catch(function () { /* watchConnection() covers the screen */ })
      .then(function () { inFlight = false; });
  }

  // ---- the stop rail: ACİL STOP and DURDUR (render.render_rail) -----------
  // Buttons marked data-instant post on pointerdown, as AXIS's toolbar estop
  // fires on the press: no click to lose, no waiting for a release. The
  // click that follows the press is not sent again; a click with no
  // pointerdown before it (detail 0: Enter or Space) is the keyboard's, and
  // is sent. The band under the buttons says at once that it went, until
  // the server's answer is on the screen.
  var sent = document.getElementById("rail-sent");
  var sentToken = 0;        // the latest press with a band
  var sentAnswered = -1;    // the latest press the server has answered
  var lastInstantDown = 0;

  function showSent(text) {
    sentToken++;
    if (sent) { sent.textContent = text; sent.hidden = false; }
    return sentToken;
  }

  function hideSent() {
    if (sent) { sent.hidden = true; sent.textContent = ""; }
  }

  function fireInstant(action) {
    if (action === "software_estop" && overlay) { sendLostEstop(); return; }
    var token = showSent(action === "software_estop"
      ? "ACİL STOP GÖNDERİLDİ — onay bekleniyor" : "DURDUR gönderildi");
    post(action, "", function () { sentAnswered = token; }, hideSent);
  }

  // The rail's own button for a key: a disabled one (DURDUR in observer
  // mode) means the key sends nothing either.
  function railButton(action) {
    return document.querySelector('#rail button[data-action="' + action + '"]');
  }

  document.addEventListener("pointerdown", function (ev) {
    if (app.contains(ev.target)) pointerPressed(ev);
    if (ev.button !== 0) return;
    var btn = ev.target.closest && ev.target.closest("button[data-instant]");
    if (!btn || btn.disabled) return;
    lastInstantDown = Date.now();
    fireInstant(btn.getAttribute("data-action"));
  }, true);

  // Esc = DURDUR, as in AXIS (axis.py binds <Escape> to task_stop), and
  // Pause = ACİL STOP: wherever the focus is, the MDI field included. The
  // kiosk keymap leaves both keys alone (deploy/files/xkb). Not on key
  // repeat: one press, one command.
  document.addEventListener("keydown", function (ev) {
    var action = ev.key === "Escape" ? "stop" : (ev.key === "Pause" ? "software_estop" : "");
    if (!action) return;
    ev.preventDefault();
    if (ev.repeat) return;
    if (action === "software_estop" && overlay) { sendLostEstop(); return; }
    var btn = railButton(action);
    if (!btn || btn.disabled) return;
    fireInstant(action);
  }, true);

  document.addEventListener("click", function (ev) {
    // Buttons only. The MDI <form> carries data-action="mdi" for the submit
    // handler below; matching any [data-action] made every click into the MDI
    // field post an empty MDI line, which logged "(boş)" and overwrote the
    // message line (found in sim/ on 2026-10-19).
    var btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    var action = btn.getAttribute("data-action");
    var value = btn.getAttribute("data-value") || "";

    if (btn.hasAttribute("data-instant")) {
      ev.preventDefault();
      if (ev.detail === 0 && Date.now() - lastInstantDown > INSTANT_GUARD_MS) {
        fireInstant(action);
      }
      return;
    }
    if (action === "screen") {
      screen = value;
      ev.preventDefault();
      refresh();
      return;
    }
    if (action === "program_page") {
      programPage = parseInt(value, 10) || 0;
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
