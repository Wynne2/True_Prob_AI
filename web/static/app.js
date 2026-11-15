// Theme switch (remembered per browser) and confirmation for destructive forms.
(function () {
  var root = document.documentElement;
  var stored = null;
  try { stored = localStorage.getItem("tp-theme"); } catch (e) {}
  if (stored) root.setAttribute("data-theme", stored);

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector("[data-theme-toggle]");
    function label() {
      if (btn) btn.textContent = root.getAttribute("data-theme") === "light" ? "Switch to dark mode" : "Switch to light mode";
    }
    label();
    if (btn) btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("tp-theme", next); } catch (e) {}
      label();
    });
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        if (!window.confirm(form.getAttribute("data-confirm"))) ev.preventDefault();
      });
    });
  });
})();
