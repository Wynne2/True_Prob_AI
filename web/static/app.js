// Theme switch and confirmation for destructive forms. The theme itself is applied by a small inline
// script in <head> before first paint (web/app.py THEME_SCRIPT); this file only handles changes.
(function () {
  var root = document.documentElement;
  var KEY = "tp-theme";
  var light = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function system() { return light && light.matches ? "light" : "dark"; }
  function stored() {
    try { var t = localStorage.getItem(KEY); return t === "light" || t === "dark" ? t : null; } catch (e) { return null; }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector("[data-theme-toggle]");
    function label() {
      if (!btn) return;
      var lightOn = root.getAttribute("data-theme") === "light";
      btn.textContent = lightOn ? "Switch to dark mode" : "Switch to light mode";
      // pressed = light theme is on (WAI-ARIA Toggle Button Pattern).
      btn.setAttribute("aria-pressed", lightOn ? "true" : "false");
    }
    label();
    if (btn) btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      // Picking what the OS already shows means "follow the OS" again.
      try { if (next === system()) localStorage.removeItem(KEY); else localStorage.setItem(KEY, next); } catch (e) {}
      label();
    });
    if (light && light.addEventListener) light.addEventListener("change", function () {
      if (!stored()) { root.setAttribute("data-theme", system()); label(); }
    });
    document.querySelectorAll("[data-reveal]").forEach(function (toggle) {
      var input = document.getElementById(toggle.getAttribute("data-reveal"));
      if (!input) return;
      toggle.hidden = false;
      toggle.addEventListener("click", function () {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        toggle.textContent = show ? "Hide" : "Show";
        toggle.setAttribute("aria-pressed", show ? "true" : "false");
        toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
      toggle.setAttribute("aria-label", "Show password");
      // Password managers recognise the field only if it is type=password when the form is sent.
      if (input.form) input.form.addEventListener("submit", function () { input.type = "password"; });
    });
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        if (!window.confirm(form.getAttribute("data-confirm"))) ev.preventDefault();
      });
    });
  });
})();
