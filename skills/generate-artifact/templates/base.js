(function () {
  var KEY = "ga-lang";
  var doc = document.documentElement;
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "en" || saved === "th") doc.lang = saved;
  } catch (e) { /* storage blocked: keep the page default */ }

  function showLevel(root, level) {
    root.querySelectorAll("[data-level]").forEach(function (box) { box.hidden = box.dataset.level !== level; });
    root.querySelectorAll("[data-level-btn]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.levelBtn === level)); });
  }

  document.addEventListener("click", function (e) {
    var langBtn = e.target.closest("[data-set-lang]");
    if (langBtn) {
      doc.lang = langBtn.dataset.setLang;
      try { localStorage.setItem(KEY, doc.lang); } catch (err) { /* ignore */ }
      return;
    }
    var tog = e.target.closest("[data-toggle]");
    if (tog) {
      var targets = document.querySelectorAll('[data-group="' + tog.dataset.toggle + '"]');
      var show = tog.getAttribute("aria-expanded") !== "true";
      targets.forEach(function (el) { el.hidden = !show; });
      tog.setAttribute("aria-expanded", String(show));
      return;
    }
    var lv = e.target.closest("[data-level-btn]");
    if (lv) showLevel(lv.closest(".ba"), lv.dataset.levelBtn);
  });
})();
