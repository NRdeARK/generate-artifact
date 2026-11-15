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

  /* diagram viewer: in-page overlay, wheel/pinch zoom, drag to pan; no popups, no Fullscreen API */
  var viewer, stage, vimg, caption, lastTrigger, pinch = 0, moved = false;
  var pointers = {};
  var view = { s: 1, x: 0, y: 0, fit: 1, w: 1, h: 1 };

  function apply() { vimg.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.s + ")"; }

  function fit() {
    var r = stage.getBoundingClientRect();
    view.w = vimg.naturalWidth || 1;
    view.h = vimg.naturalHeight || 1;
    vimg.style.width = view.w + "px";
    vimg.style.height = view.h + "px";
    view.fit = Math.min(r.width / view.w, r.height / view.h) * 0.96;
    view.s = view.fit;
    view.x = (r.width - view.w * view.s) / 2;
    view.y = (r.height - view.h * view.s) / 2;
    apply();
  }

  function zoomAt(factor, cx, cy) {
    var s = Math.min(Math.max(view.s * factor, view.fit * 0.5), view.fit * 12);
    var k = s / view.s;
    view.x = cx - (cx - view.x) * k;
    view.y = cy - (cy - view.y) * k;
    view.s = s;
    apply();
  }

  function zoomCenter(factor) {
    var r = stage.getBoundingClientRect();
    zoomAt(factor, r.width / 2, r.height / 2);
  }

  function openViewer(frame) {
    viewer = document.getElementById("viewer");
    stage = document.getElementById("viewer-stage");
    vimg = viewer.querySelector("img");
    caption = document.getElementById("viewer-caption");
    var img = frame.querySelector("img");
    lastTrigger = frame.querySelector(".expand") || img;
    caption.textContent = img.alt || "";
    viewer.hidden = false;
    doc.classList.add("viewer-open");
    vimg.onload = fit;
    vimg.src = img.src;
    if (vimg.complete && vimg.naturalWidth) fit();
    viewer.querySelector('[data-viewer="close"]').focus();
  }

  function closeViewer() {
    viewer.hidden = true;
    doc.classList.remove("viewer-open");
    if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
  }

  function viewerOpen() { return viewer && !viewer.hidden; }

  function pinchDistance() {
    var ids = Object.keys(pointers);
    if (ids.length < 2) return 0;
    var a = pointers[ids[0]], b = pointers[ids[1]];
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  document.addEventListener("keydown", function (e) {
    if (!viewerOpen()) return;
    if (e.key === "Escape") { closeViewer(); return; }
    if (e.key === "+" || e.key === "=") zoomCenter(1.25);
    else if (e.key === "-") zoomCenter(0.8);
    else if (e.key === "0") fit();
    else if (e.key === "Tab") {
      var buttons = viewer.querySelectorAll("button");
      var first = buttons[0], last = buttons[buttons.length - 1];
      if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
    }
  });

  document.addEventListener("wheel", function (e) {
    if (!viewerOpen() || !stage.contains(e.target)) return;
    e.preventDefault();
    var r = stage.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  document.addEventListener("pointerdown", function (e) {
    if (!viewerOpen() || e.target.closest(".viewer-bar") || !stage.contains(e.target)) return;
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    moved = false;
    pinch = pinchDistance();
    stage.classList.add("grabbing");
  });

  document.addEventListener("pointermove", function (e) {
    var prev = pointers[e.pointerId];
    if (!prev || !viewerOpen()) return;
    var count = Object.keys(pointers).length;
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (count >= 2) {
      var d = pinchDistance();
      var r = stage.getBoundingClientRect();
      if (pinch) zoomAt(d / pinch, e.clientX - r.left, e.clientY - r.top);
      pinch = d;
    } else {
      view.x += e.clientX - prev.x;
      view.y += e.clientY - prev.y;
      apply();
    }
    moved = true;
  });

  function endPointer(e) {
    delete pointers[e.pointerId];
    pinch = 0;
    if (stage) stage.classList.remove("grabbing");
  }
  document.addEventListener("pointerup", endPointer);
  document.addEventListener("pointercancel", endPointer);

  document.addEventListener("click", function (e) {
    if (viewerOpen()) {
      var action = e.target.closest("[data-viewer]");
      if (action) {
        var name = action.dataset.viewer;
        if (name === "close") closeViewer();
        else if (name === "in") zoomCenter(1.25);
        else if (name === "out") zoomCenter(0.8);
        else fit();
      } else if (e.target === stage && !moved) {
        closeViewer();
      }
      return;
    }
    var expand = e.target.closest(".diagram .expand, .diagram img");
    if (expand) { openViewer(expand.closest(".diagram-frame")); return; }
  });

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

  /* side index scrollspy (Tab 3 concepts, Tab 4 use cases) and sticky offsets */
  function initSideIndex() {
    var header = document.querySelector(".top");
    if (header) doc.style.setProperty("--header-h", header.offsetHeight + "px");
    var links = Array.prototype.slice.call(document.querySelectorAll('.side a[href^="#"]'));
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
    if (!links.length || targets.indexOf(null) !== -1) return;
    function update() {
      var line = window.innerHeight * 0.3;
      var current = 0;
      targets.forEach(function (t, i) { if (t.getBoundingClientRect().top <= line) current = i; });
      links.forEach(function (a, i) {
        a.classList.toggle("active", i === current);
        if (i === current) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      });
    }
    var queued = false;
    function schedule() {
      if (queued) return;
      queued = true;
      setTimeout(function () { queued = false; update(); }, 30);
    }
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", function () { if (header) doc.style.setProperty("--header-h", header.offsetHeight + "px"); schedule(); });
    if ("IntersectionObserver" in window) {
      try {
        var io = new IntersectionObserver(schedule, { rootMargin: "0px 0px -60% 0px" });
        targets.forEach(function (t) { io.observe(t); });
      } catch (err) { /* the scroll listener above still works */ }
    }
    update();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initSideIndex);
  else initSideIndex();
})();
