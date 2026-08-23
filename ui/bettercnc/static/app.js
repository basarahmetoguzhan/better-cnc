/* Thin client. All rendering happens on the server; this file only
   forwards intents and swaps in the returned HTML.
   Deliberately minimal: keeping the view logic in Python is what makes the
   safety rules testable without a browser. */

(function () {
  "use strict";

  var app = document.getElementById("app");
  var screen = (window.__VM__ && window.__VM__.screen) || "kontrol";
  var polling = true;

  function post(action, value) {
    return fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, value: value, screen: screen })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { swap(data); })
      .catch(function () { /* server gone; the poll will surface it */ });
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

    if (focusName) {
      var again = app.querySelector('[name="' + focusName + '"]');
      if (again) {
        again.focus();
        if (value != null) again.value = value;
        if (caret != null && again.setSelectionRange) {
          try { again.setSelectionRange(caret, caret); } catch (e) {}
        }
      }
    }
  }

  function refresh() {
    if (!polling) return;
    fetch("/api/render?screen=" + encodeURIComponent(screen))
      .then(function (r) { return r.json(); })
      .then(swap)
      .catch(function () {});
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-action]");
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

  setInterval(refresh, 1000);
})();
