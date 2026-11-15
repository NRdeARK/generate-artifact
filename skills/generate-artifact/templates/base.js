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

  /* ---- sequence diagrams: alt fragments shown or hidden in place, the diagram reflows ---- */
  function isShown(el) { return el.getAttribute("display") !== "none"; }

  function reflow(svg) {
    var cursor = +svg.dataset.top;
    svg.querySelectorAll("[data-block]").forEach(function (b) {
      if (!isShown(b)) return;
      b.setAttribute("transform", "translate(0," + (cursor - +b.dataset.y) + ")");
      cursor += +b.dataset.h;
    });
    svg.querySelectorAll("[data-lifeline]").forEach(function (l) { l.setAttribute("y2", cursor); });
    var legend = svg.querySelector("[data-legend]");
    var legendTop = cursor + 40;
    if (legend) legend.setAttribute("transform", "translate(0," + (legendTop - +legend.dataset.y) + ")");
    svg.setAttribute("viewBox", "0 0 " + svg.dataset.w + " " + (legendTop + +svg.dataset.legendH));
  }

  function refreshToggles(svg) {
    document.querySelectorAll('[data-target="' + svg.id + '"]').forEach(function (btn) {
      if (btn.dataset.fragment) {
        var f = svg.querySelector('[data-fragment="' + btn.dataset.fragment + '"]');
        btn.setAttribute("aria-pressed", String(!!f && isShown(f)));
      } else if (btn.dataset.kind) {
        var all = Array.prototype.slice.call(svg.querySelectorAll('[data-fragment][data-kind="' + btn.dataset.kind + '"]'));
        btn.setAttribute("aria-pressed", String(all.length > 0 && all.every(isShown)));
      }
    });
  }

  function toggleFragments(btn) {
    var svg = document.getElementById(btn.dataset.target);
    if (!svg) return;
    var frags;
    if (btn.dataset.fragment) {
      frags = [svg.querySelector('[data-fragment="' + btn.dataset.fragment + '"]')];
    } else {
      frags = Array.prototype.slice.call(svg.querySelectorAll('[data-fragment][data-kind="' + btn.dataset.kind + '"]'));
    }
    frags = frags.filter(Boolean);
    var show = !frags.every(isShown);
    frags.forEach(function (f) { if (show) f.removeAttribute("display"); else f.setAttribute("display", "none"); });
    reflow(svg);
    refreshToggles(svg);
  }

  function expandAllFragments() {
    document.querySelectorAll("svg[data-reflow]").forEach(function (svg) {
      svg.querySelectorAll("[data-fragment]").forEach(function (f) { f.removeAttribute("display"); });
      reflow(svg);
    });
  }

  /* ---- diagram viewer: in-page overlay, one pane or two (compare), wheel/pinch zoom, drag to pan.
         No popups, no Fullscreen API (claude.ai pages run in a sandboxed frame). ---- */
  var viewer, panes = [], sync = true, active = 0, lastTrigger, moved = false;
  var pointers = {};
  var pinch = { pane: -1, dist: 0 };

  function initViewer() {
    viewer = document.getElementById("viewer");
    panes = [0, 1].map(function (i) {
      var el = viewer.querySelector('[data-pane="' + i + '"]');
      return { el: el, stage: el.querySelector(".viewer-stage"), box: el.querySelector(".viewer-img"), label: el.querySelector(".pane-label"),
               v: { s: 1, x: 0, y: 0, fit: 1, w: 1, h: 1 } };
    });
  }

  function apply(p) { p.box.style.transform = "translate(" + p.v.x + "px," + p.v.y + "px) scale(" + p.v.s + ")"; }

  function livePanes() { return panes.filter(function (p) { return !p.el.hidden; }); }

  function mirror(from) {
    if (!sync) return;
    livePanes().forEach(function (p) {
      if (p === from) return;
      p.v.s = from.v.s; p.v.x = from.v.x; p.v.y = from.v.y;
      apply(p);
    });
  }

  function fitPane(p) {
    var r = p.stage.getBoundingClientRect();
    p.v.fit = Math.min(r.width / p.v.w, r.height / p.v.h) * 0.96;
    p.v.s = p.v.fit;
    p.v.x = (r.width - p.v.w * p.v.s) / 2;
    p.v.y = (r.height - p.v.h * p.v.s) / 2;
    apply(p);
  }

  function fitAll() { livePanes().forEach(fitPane); }

  function zoomAt(p, factor, cx, cy) {
    var s = Math.min(Math.max(p.v.s * factor, p.v.fit * 0.5), p.v.fit * 12);
    var k = s / p.v.s;
    p.v.x = cx - (cx - p.v.x) * k;
    p.v.y = cy - (cy - p.v.y) * k;
    p.v.s = s;
    apply(p);
    mirror(p);
  }

  function zoomCenter(factor) {
    var p = panes[active].el.hidden ? panes[0] : panes[active];
    var r = p.stage.getBoundingClientRect();
    zoomAt(p, factor, r.width / 2, r.height / 2);
  }

  function mount(p, svg, label) {
    var n = panes.indexOf(p);
    var html = svg.outerHTML
      .replace(/ id="/g, ' id="v' + n + '-')
      .replace(/url\(#/g, "url(#v" + n + "-")
      .replace(/aria-labelledby="([^"]*)"/g, function (m, ids) {
        return 'aria-labelledby="' + ids.split(" ").map(function (i) { return "v" + n + "-" + i; }).join(" ") + '"';
      });
    p.box.innerHTML = html;
    var clone = p.box.firstElementChild;
    var vb = clone.viewBox.baseVal;
    p.v.w = vb.width || 1;
    p.v.h = vb.height || 1;
    clone.removeAttribute("style");
    clone.style.cssText = "width:100%;height:100%;display:block;max-width:none";
    p.box.style.width = p.v.w + "px";
    p.box.style.height = p.v.h + "px";
    p.label.textContent = label || "";
    p.label.hidden = !label;
  }

  function titleOf(svg) {
    var t = svg.querySelector("title");
    return t ? t.textContent : "";
  }

  function openViewer(svgs, labels, trigger) {
    if (!viewer) initViewer();
    lastTrigger = trigger;
    var compare = svgs.length > 1;
    panes[0].el.hidden = false;
    panes[1].el.hidden = !compare;
    viewer.querySelector('[data-viewer="sync"]').hidden = !compare;
    sync = true;
    active = 0;
    viewer.querySelector('[data-viewer="sync"]').setAttribute("aria-pressed", "true");
    mount(panes[0], svgs[0], compare ? labels[0] : "");
    if (compare) mount(panes[1], svgs[1], labels[1]);
    document.getElementById("viewer-caption").textContent = compare ? labels.join(" / ") : titleOf(svgs[0]);
    viewer.hidden = false;
    doc.classList.add("viewer-open");
    fitAll();
    viewer.querySelector('[data-viewer="close"]').focus();
  }

  function closeViewer() {
    viewer.hidden = true;
    doc.classList.remove("viewer-open");
    panes.forEach(function (p) { p.box.innerHTML = ""; });
    if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
  }

  function viewerOpen() { return viewer && !viewer.hidden; }

  function paneOf(target) {
    var el = target.closest && target.closest(".viewer-pane");
    return el ? panes[+el.dataset.pane] : null;
  }

  document.addEventListener("keydown", function (e) {
    if (!viewerOpen()) return;
    if (e.key === "Escape") { closeViewer(); return; }
    if (e.key === "+" || e.key === "=") zoomCenter(1.25);
    else if (e.key === "-") zoomCenter(0.8);
    else if (e.key === "0") fitAll();
    else if (e.key === "Tab") {
      var buttons = Array.prototype.filter.call(viewer.querySelectorAll("button"), function (b) { return !b.hidden; });
      var first = buttons[0], last = buttons[buttons.length - 1];
      if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
    }
  });

  document.addEventListener("wheel", function (e) {
    if (!viewerOpen()) return;
    var p = paneOf(e.target);
    if (!p) return;
    e.preventDefault();
    active = panes.indexOf(p);
    var r = p.stage.getBoundingClientRect();
    zoomAt(p, Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  function pinchDistance(pane) {
    var ids = Object.keys(pointers).filter(function (id) { return pointers[id].pane === pane; });
    if (ids.length < 2) return 0;
    var a = pointers[ids[0]], b = pointers[ids[1]];
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  document.addEventListener("pointerdown", function (e) {
    if (!viewerOpen()) return;
    var p = paneOf(e.target);
    if (!p || e.target.closest(".viewer-bar")) return;
    var n = panes.indexOf(p);
    active = n;
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY, pane: n };
    moved = false;
    pinch = { pane: n, dist: pinchDistance(n) };
    p.stage.classList.add("grabbing");
  });

  document.addEventListener("pointermove", function (e) {
    var prev = pointers[e.pointerId];
    if (!prev || !viewerOpen()) return;
    var p = panes[prev.pane];
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY, pane: prev.pane };
    var d = pinchDistance(prev.pane);
    if (d) {
      var r = p.stage.getBoundingClientRect();
      if (pinch.dist) zoomAt(p, d / pinch.dist, e.clientX - r.left, e.clientY - r.top);
      pinch.dist = d;
    } else {
      p.v.x += e.clientX - prev.x;
      p.v.y += e.clientY - prev.y;
      apply(p);
      mirror(p);
    }
    moved = true;
  });

  function endPointer(e) {
    delete pointers[e.pointerId];
    pinch.dist = 0;
    panes.forEach(function (p) { if (p) p.stage.classList.remove("grabbing"); });
  }
  document.addEventListener("pointerup", endPointer);
  document.addEventListener("pointercancel", endPointer);

  function labelOf(strong) {
    var span = strong.querySelector('[data-l="' + doc.lang + '"]');
    return (span || strong).textContent;
  }

  function hasSelection() {
    try { return String(window.getSelection()).length > 0; } catch (err) { return false; }
  }

  document.addEventListener("click", function (e) {
    if (viewerOpen()) {
      var action = e.target.closest("[data-viewer]");
      if (action) {
        var name = action.dataset.viewer;
        if (name === "close") closeViewer();
        else if (name === "in") zoomCenter(1.25);
        else if (name === "out") zoomCenter(0.8);
        else if (name === "sync") {
          sync = !sync;
          action.setAttribute("aria-pressed", String(sync));
          if (sync) mirror(panes[active]);
        } else fitAll();
      } else if (e.target.classList.contains("viewer-stage") && !moved) {
        closeViewer();
      }
      return;
    }
    var both = e.target.closest("[data-compare]");
    if (both) {
      var pair = both.closest(".ba-cols");
      var figures = pair.querySelectorAll(".diagram-frame > svg");
      var heads = pair.querySelectorAll(".pair-side > strong");
      if (figures.length === 2) openViewer([figures[0], figures[1]], [labelOf(heads[0]), labelOf(heads[1])], both);
      return;
    }
    var one = e.target.closest(".diagram .expand, .diagram-frame > svg");
    if (one && !hasSelection()) {
      var frame = one.closest(".diagram-frame");
      openViewer([frame.querySelector(":scope > svg")], [], frame.querySelector(".expand") || one);
    }
  });

  document.addEventListener("click", function (e) {
    var langBtn = e.target.closest("[data-set-lang]");
    if (langBtn) {
      doc.lang = langBtn.dataset.setLang;
      try { localStorage.setItem(KEY, doc.lang); } catch (err) { /* ignore */ }
      return;
    }
    var tog = e.target.closest("[data-target]");
    if (tog) { toggleFragments(tog); return; }
    var lv = e.target.closest("[data-level-btn]");
    if (lv) showLevel(lv.closest(".ba"), lv.dataset.levelBtn);
  });

  /* ---- side index scrollspy (Tab 3 concepts, Tab 4 use cases) and sticky offsets ---- */
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

  function init() {
    initSideIndex();
    if (document.body.classList.contains("print")) expandAllFragments();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
