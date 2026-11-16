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
      if (btn) btn.textContent = root.getAttribute("data-theme") === "light" ? "Switch to dark mode" : "Switch to light mode";
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
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        if (!window.confirm(form.getAttribute("data-confirm"))) ev.preventDefault();
      });
    });
  });
})();
