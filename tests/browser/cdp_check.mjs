#!/usr/bin/env node
// Real-browser checks over the Chrome DevTools Protocol (no npm packages; Node 22+ has a global WebSocket).
//   node cdp_check.mjs --browser <edge-or-chrome.exe> --pages <dir with intent.html, tab1..tab5.html> --shots <screenshot dir>
// Prints one line: RESULT:{"<check>": {"ok": true|false, "detail": "..."}, ..., "_shots": [paths]}
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, all) => (v.startsWith("--") ? [...acc, [v.slice(2), all[i + 1]]] : acc), []));
const { browser, pages, shots } = args;
if (!browser || !pages || !shots) { console.error("usage: --browser <exe> --pages <dir> --shots <dir>"); process.exit(2); }
fs.mkdirSync(shots, { recursive: true });

const PAGES = ["intent", "tab1", "tab2", "tab3", "tab4", "tab5"];
const results = {};
const shotPaths = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const record = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail: String(detail) }; };

// ---------------------------------------------------------------- browser + protocol
const userDir = fs.mkdtempSync(path.join(os.tmpdir(), "cdp-profile-"));
const proc = spawn(browser, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
  `--user-data-dir=${userDir}`, "about:blank"], { stdio: "ignore" });
const watchdog = setTimeout(() => {
  console.error("cdp_check timed out");
  record("harness.error", false, "global watchdog: the run took longer than 240s");
  results._shots = shotPaths;
  console.log("RESULT:" + JSON.stringify(results));  // partial results still tell which checks passed
  cleanup(3);
}, 240000);

function cleanup(code) {
  clearTimeout(watchdog);
  try { proc.kill(); } catch { /* already gone */ }
  setTimeout(() => { try { fs.rmSync(userDir, { recursive: true, force: true }); } catch { /* profile still locked */ } process.exit(code); }, 400);
}

let port = null;
for (let i = 0; i < 100 && !port; i++) {
  try { port = fs.readFileSync(path.join(userDir, "DevToolsActivePort"), "utf8").split("\n")[0]; } catch { await sleep(150); }
}
if (!port) { console.error("browser did not expose a DevTools port"); cleanup(2); await sleep(1000); }
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((resolve) => ws.addEventListener("open", resolve));

let nextId = 1;
const pending = new Map();
const waiters = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(`${msg.error.message}`)) : resolve(msg.result);
  } else if (msg.method) {
    waiters.filter((w) => w.method === msg.method).forEach((w) => w.resolve(msg.params));
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`no answer to ${method} within 30s`)); }, 30000);
  pending.set(id, { resolve: (v) => { clearTimeout(timer); resolve(v); }, reject: (e) => { clearTimeout(timer); reject(e); } });
  ws.send(JSON.stringify({ id, method, params }));
});
const waitEvent = (method, ms = 20000) => new Promise((resolve, reject) => {
  const waiter = { method, resolve };
  waiters.push(waiter);
  setTimeout(() => reject(new Error(`timeout waiting for ${method}`)), ms);
});
const ev = async (expression) => {
  const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
};

await send("Page.enable");
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1300, height: 1000 };
let CW = 1300;  // emulated device width in CSS px (innerWidth is unreliable once content overflows on mobile)
async function emulate(kind, size = PHONE) {
  CW = kind === "phone" ? size.width : DESKTOP.width;
  if (kind === "phone") {
    await send("Emulation.setDeviceMetricsOverride", { ...size, deviceScaleFactor: 3, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  } else {
    await send("Emulation.setDeviceMetricsOverride", { ...DESKTOP, deviceScaleFactor: 1, mobile: false });
    await send("Emulation.setTouchEmulationEnabled", { enabled: false });
  }
}

async function goto(page, { lang } = {}) {
  const loaded = waitEvent("Page.loadEventFired");
  await send("Page.navigate", { url: pathToFileURL(path.join(pages, `${page}.html`)).href });
  await loaded;
  if (lang) await ev(`document.documentElement.lang = ${JSON.stringify(lang)}`);
  await ev("document.fonts.ready.then(() => true)");
  await sleep(300);
}

async function screenshot(name, selector = null) {
  const file = path.join(shots, `${name}.png`);
  let clip;
  if (selector) {
    const r = await ev(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); const b = e.getBoundingClientRect(); return { x: 0, y: b.y + scrollY, w: ${CW}, h: b.height }; })()`);
    clip = { x: r.x, y: r.y, width: r.w, height: Math.min(r.h, 6000), scale: 1 };
  } else {
    const metrics = await send("Page.getLayoutMetrics");
    clip = { x: 0, y: 0, width: Math.min(metrics.cssContentSize.width, CW), height: Math.min(metrics.cssContentSize.height, 6000), scale: 1 };
  }
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip });
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  shotPaths.push(path.resolve(file));
  return file;
}

// ---------------------------------------------------------------- page probes (run inside the page)
const overflowJs = () => `(() => {
  const vw = ${CW}, bad = [];
  document.querySelectorAll("body *").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || r.right <= vw + 1 || el.closest("svg")) return;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === "auto" || o === "scroll" || o === "hidden") return;
    }
    bad.push(el.tagName + "." + String(el.className).slice(0, 30) + " right=" + Math.round(r.right));
  });
  return { sw: document.documentElement.scrollWidth, vw, bad: bad.slice(0, 6) };
})()`;

const VIEWER = `(() => {
  const v = document.getElementById("viewer");
  const parse = (t) => { const m = /translate\\(([-\\d.e]+)px,\\s*([-\\d.e]+)px\\)\\s*scale\\(([-\\d.e]+)\\)/.exec(t); return m ? { x: +m[1], y: +m[2], s: +m[3] } : null; };
  const imgs = [...v.querySelectorAll(".viewer-img")];
  const pane = (i) => { const r = v.querySelectorAll(".viewer-pane")[i].getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, hidden: v.querySelectorAll(".viewer-pane")[i].hidden }; };
  return { open: !v.hidden, p0: parse(imgs[0].style.transform), p1: parse(imgs[1].style.transform), pane0: pane(0), pane1: pane(1),
    zoom: document.querySelector("[data-viewer=sync-zoom]").getAttribute("aria-pressed"), pan: document.querySelector("[data-viewer=sync-pan]").getAttribute("aria-pressed"),
    layout: v.querySelector(".viewer-panes").dataset.layout };
})()`;

const rectOf = (selector, scroll = true) => ev(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); ${scroll ? "e.scrollIntoView({ block: 'center' });" : ""}
  const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, left: r.x, top: r.y }; })()`);

// ---------------------------------------------------------------- touch helpers (Input.dispatchTouchEvent)
const touch = (type, points) => send("Input.dispatchTouchEvent", { type, touchPoints: points.map((p, i) => ({ x: p.x, y: p.y, id: p.id ?? i + 1 })) });
async function tap(x, y) { await touch("touchStart", [{ x, y }]); await sleep(50); await touch("touchEnd", []); await sleep(250); }
async function drag(x, y, dx, dy, steps = 8) {
  await touch("touchStart", [{ x, y }]);
  for (let i = 1; i <= steps; i++) { await touch("touchMove", [{ x: x + (dx * i) / steps, y: y + (dy * i) / steps }]); await sleep(20); }
  await touch("touchEnd", []);
  await sleep(200);
}
async function pinch(cx, cy, from, to, steps = 8) {
  const pts = (d) => [{ x: cx - d / 2, y: cy, id: 1 }, { x: cx + d / 2, y: cy, id: 2 }];
  await touch("touchStart", pts(from));
  for (let i = 1; i <= steps; i++) { await touch("touchMove", pts(from + ((to - from) * i) / steps)); await sleep(20); }
  await touch("touchEnd", []);
  await sleep(200);
}
const near = (a, b, tol = 1.5) => Math.abs(a - b) <= tol;

async function main() {
  // ============================================================ (1) phone layout, all six pages
  await emulate("phone");
  for (const page of PAGES) {
    await goto(page);
    const o = await ev(overflowJs());
    record(`phone.no_horizontal_overflow.${page}`, o.sw === o.vw && o.bad.length === 0, `scrollWidth ${o.sw} vs viewport ${o.vw}; offenders: ${o.bad.join(" | ") || "none"}`);
    await screenshot(`phone-${page}`);
  }
  // the real-device scenario: a 360px-wide Android phone; the page must be exactly as wide as the screen
  await emulate("phone", { width: 360, height: 800 });
  for (const page of PAGES) {
    await goto(page);
    const o = await ev(overflowJs());
    record(`phone360.no_horizontal_overflow.${page}`, o.sw === o.vw && o.bad.length === 0, `scrollWidth ${o.sw} vs viewport ${o.vw}; offenders: ${o.bad.join(" | ") || "none"}`);
    if (page === "tab1") await screenshot("phone360-tab1");
  }
  await emulate("phone");
  await goto("tab5");
  const nav = await ev(`(() => { const n = document.querySelector(".nav"); return { ox: getComputedStyle(n).overflowX, sw: n.scrollWidth, cw: n.clientWidth }; })()`);
  record("phone.tab_bar_scrolls_sideways", (nav.ox === "auto" || nav.ox === "scroll") && nav.sw > nav.cw, `overflow-x ${nav.ox}, scrollWidth ${nav.sw} > clientWidth ${nav.cw}`);
  for (const page of ["tab3", "tab4"]) {
    await goto(page);
    const s = await ev(`(() => { const a = document.querySelector(".side").getBoundingClientRect(), m = document.querySelector(".main-col").getBoundingClientRect(); return { aBottom: a.bottom, mTop: m.top, aw: a.width, vw: ${CW} }; })()`);
    record(`phone.side_index_stacks_on_top.${page}`, s.aBottom <= s.mTop + 1 && s.aw >= s.vw - 40 && s.aw <= s.vw, `side bottom ${s.aBottom.toFixed(1)} <= content top ${s.mTop.toFixed(1)}, side width ${s.aw.toFixed(1)}`);
  }
  for (const page of ["tab1", "tab5"]) {
    await goto(page);
    const p = await ev(`(() => [...document.querySelectorAll(".ba-cols")].filter((c) => !c.hidden).map((c) => { const [a, b] = [...c.querySelectorAll(":scope > .pair-side")].map((e) => e.getBoundingClientRect()); return { stacked: Math.abs(a.left - b.left) < 2 && b.top >= a.bottom - 1, aw: a.width, vw: ${CW} }; }))()`);
    record(`phone.pairs_stacked.${page}`, p.length > 0 && p.every((x) => x.stacked && x.aw <= x.vw), `${p.length} pair(s): ${JSON.stringify(p)}`);
  }
  for (const page of ["tab1", "tab4", "tab5"]) {
    await goto(page);
    const cards = await ev(`(() => { const cells = [...document.querySelectorAll("table.stack td")]; const heads = [...document.querySelectorAll("table.stack .stack-head")];
      const label = document.querySelector("table.stack .cell-label");
      return { cells: cells.length, block: cells.every((c) => getComputedStyle(c).display === "block"), headsHidden: heads.every((h) => getComputedStyle(h).display === "none"), labelShown: !!label && getComputedStyle(label).display !== "none" }; })()`);
    record(`phone.key_tables_render_as_cards.${page}`, cards.cells > 0 && cards.block && cards.headsHidden && cards.labelShown, JSON.stringify(cards));
    const t = await ev(`(() => { const tables = [...document.querySelectorAll("table")]; const wraps = [...new Set(tables.map((t) => t.closest(".table-wrap")))].filter(Boolean);
      return { tables: tables.length, wrapped: tables.every((t) => t.closest(".table-wrap")), auto: wraps.every((w) => getComputedStyle(w).overflowX === "auto"), scrolls: wraps.filter((w) => w.scrollWidth > w.clientWidth).length,
      allStacked: tables.every((t) => t.classList.contains("stack")) }; })()`);
    // every table lives in a scroll box; wide ones scroll inside it, the key ones are cards (nothing to scroll)
    record(`phone.tables_scroll_in_their_box.${page}`, t.tables > 0 && t.wrapped && t.auto && (t.scrolls > 0 || t.allStacked), JSON.stringify(t));
  }

  await goto("tab1");
  const crud = await ev(`(() => { const t = document.querySelector(".crud-table"); const w = t.closest(".table-wrap"); const td = t.querySelector("tr:nth-child(2) td:first-child");
    return { sticky: getComputedStyle(td).position, left: getComputedStyle(td).left, scrolls: w.scrollWidth > w.clientWidth, overflow: getComputedStyle(w).overflowX, stacked: getComputedStyle(td).display === "block" }; })()`);
  record("phone.crud_matrix_scrolls_with_sticky_first_column", crud.sticky === "sticky" && crud.scrolls && crud.overflow === "auto" && !crud.stacked, JSON.stringify(crud));

  // ============================================================ (2) real touch on a phone: single viewer
  await goto("tab3");
  const expand = await rectOf(".expand");
  await tap(expand.x, expand.y);
  let st = await ev(VIEWER);
  record("touch.tap_expand_opens_viewer", st.open && st.p0 && st.p0.s > 0, `open=${st.open} scale=${st.p0 && st.p0.s.toFixed(3)}`);
  await screenshot("touch-viewer-open");
  const stage = await rectOf("#viewer-stage", false);
  const s0 = st.p0;
  await pinch(stage.x, stage.y, 80, 220);
  st = await ev(VIEWER);
  record("touch.two_finger_pinch_zooms", st.p0.s > s0.s * 1.5, `scale ${s0.s.toFixed(3)} -> ${st.p0.s.toFixed(3)}`);
  const before = st.p0;
  await drag(stage.x, stage.y, 60, 30);
  st = await ev(VIEWER);
  record("touch.one_finger_drag_pans", near(st.p0.x - before.x, 60, 6) && near(st.p0.y - before.y, 30, 6), `dx=${(st.p0.x - before.x).toFixed(1)} dy=${(st.p0.y - before.y).toFixed(1)}`);
  const fit = await rectOf("[data-viewer=reset]", false);
  await tap(fit.x, fit.y);
  const corner = await rectOf("#viewer-stage", false);
  await tap(corner.left + 6, corner.top + 6);
  st = await ev(VIEWER);
  record("touch.tap_backdrop_closes", !st.open, `open=${st.open}`);

  // ============================================================ (2b) real touch: compare mode, sync toggles, layout toggle
  await goto("tab5");
  const both = await rectOf(".ba-cols:not([hidden]) .expand-both");
  await tap(both.x, both.y);
  st = await ev(VIEWER);
  record("touch.compare_opens_two_panes_stacked_with_sync_off", st.open && !st.pane1.hidden && st.zoom === "false" && st.pan === "false" && st.layout === "stack",
    `open=${st.open} two=${!st.pane1.hidden} syncZoom=${st.zoom} syncPan=${st.pan} layout=${st.layout}`);
  const bar = await ev(`[...document.querySelectorAll("#viewer .viewer-bar button")].filter((b) => !b.hidden && b.offsetParent).map((b) => { const r = b.getBoundingClientRect(); return { n: b.dataset.viewer, l: r.left, r: r.right }; })`);
  record("phone.viewer_bar_controls_fit_the_screen", bar.length >= 7 && bar.every((b) => b.l >= -0.5 && b.r <= CW + 0.5), bar.map((b) => `${b.n}[${Math.round(b.l)}-${Math.round(b.r)}]`).join(" "));
  await screenshot("touch-compare-phone");
  const pane = async (i) => { const s = await ev(VIEWER); const p = i ? s.pane1 : s.pane0; return { x: p.x + p.w / 2, y: p.y + p.h / 2 }; };
  let a = (await ev(VIEWER)), c0 = await pane(0);
  await drag(c0.x, c0.y, 30, 15);
  let b = await ev(VIEWER);
  record("touch.compare_independent_when_sync_off", near(b.p0.x - a.p0.x, 30, 6) && near(b.p1.x, a.p1.x, 0.6) && near(b.p1.y, a.p1.y, 0.6), `pane0 dx=${(b.p0.x - a.p0.x).toFixed(1)}, pane1 dx=${(b.p1.x - a.p1.x).toFixed(2)}`);
  a = await ev(VIEWER);
  const panToggle = await rectOf("[data-viewer=sync-pan]", false);
  await tap(panToggle.x, panToggle.y);
  c0 = await pane(0);
  await drag(c0.x, c0.y, 30, 0);
  b = await ev(VIEWER);
  record("touch.compare_sync_pan_moves_both", a.pan === "false" && b.pan === "true" && near(b.p0.x - a.p0.x, 30, 6) && near(b.p1.x - a.p1.x, b.p0.x - a.p0.x, 1.5), `pan=${b.pan} d0=${(b.p0.x - a.p0.x).toFixed(1)} d1=${(b.p1.x - a.p1.x).toFixed(1)}`);
  const zoomToggle = await rectOf("[data-viewer=sync-zoom]", false);
  await tap(zoomToggle.x, zoomToggle.y);
  a = await ev(VIEWER);
  c0 = await pane(0);
  await pinch(c0.x, c0.y, 80, 200);
  b = await ev(VIEWER);
  record("touch.compare_sync_zoom_scales_both", b.zoom === "true" && b.p0.s > a.p0.s * 1.4 && near(b.p1.s, b.p0.s, 0.001), `zoom=${b.zoom} scales ${b.p0.s.toFixed(3)} / ${b.p1.s.toFixed(3)}`);
  for (const toggle of ["sync-zoom", "sync-pan"]) { const r = await rectOf(`[data-viewer=${toggle}]`, false); await tap(r.x, r.y); }
  a = await ev(VIEWER);
  c0 = await pane(0);
  await pinch(c0.x, c0.y, 80, 200);
  b = await ev(VIEWER);
  record("touch.compare_zoom_independent_after_sync_off", a.zoom === "false" && a.pan === "false" && b.p0.s > a.p0.s * 1.4 && near(b.p1.s, a.p1.s, 0.001), `pane0 ${a.p0.s.toFixed(3)} -> ${b.p0.s.toFixed(3)}, pane1 ${a.p1.s.toFixed(3)} -> ${b.p1.s.toFixed(3)}`);
  const lay = await rectOf("[data-viewer=layout-side]", false);
  const beforeLayout = (await ev(VIEWER)).p0;
  await tap(lay.x, lay.y);
  b = await ev(VIEWER);
  record("touch.layout_toggle_side_by_side_on_phone", b.layout === "side" && b.pane1.x > b.pane0.x + b.pane0.w - 2 && Math.abs(b.pane0.y - b.pane1.y) < 2 && (b.p0.s !== beforeLayout.s || b.p0.x !== beforeLayout.x),
    `layout=${b.layout} pane0.x=${Math.round(b.pane0.x)} pane1.x=${Math.round(b.pane1.x)} refit=${b.p0.s !== beforeLayout.s || b.p0.x !== beforeLayout.x}`);
  await screenshot("touch-compare-phone-side-by-side");
  const stackBtn = await rectOf("[data-viewer=layout-stack]", false);
  await tap(stackBtn.x, stackBtn.y);
  b = await ev(VIEWER);
  record("touch.layout_toggle_back_to_top_bottom", b.layout === "stack" && b.pane1.y >= b.pane0.y + b.pane0.h - 2, `layout=${b.layout}`);
  const x = await rectOf("[data-viewer=close]", false);
  await tap(x.x, x.y);
  st = await ev(VIEWER);
  record("touch.close_button_closes_compare", !st.open, `open=${st.open}`);

  // ============================================================ layout toggle on desktop (auto -> user choice -> remembered)
  await emulate("desktop");
  await goto("tab5");
  await ev("localStorage.removeItem('ga-viewer-layout')");
  await goto("tab5");  // the page reads the saved choice at load
  await ev(`document.querySelector(".ba-cols:not([hidden]) .expand-both").click()`);
  st = await ev(VIEWER);
  record("desktop.layout_auto_is_side_by_side", st.layout === "side" && st.pane1.x > st.pane0.x + st.pane0.w - 2, `layout=${st.layout}`);
  await screenshot("desktop-compare-side-by-side");
  const t0 = st.p0;
  await ev(`document.querySelector("[data-viewer=layout-stack]").click()`);
  st = await ev(VIEWER);
  record("desktop.layout_toggle_stack_refits_and_is_remembered", st.layout === "stack" && st.pane1.y >= st.pane0.y + st.pane0.h - 2 && (st.p0.s !== t0.s || st.p0.x !== t0.x)
    && (await ev("localStorage.getItem('ga-viewer-layout')")) === "stack", `layout=${st.layout} saved=${await ev("localStorage.getItem('ga-viewer-layout')")}`);
  await screenshot("desktop-compare-top-bottom");
  await ev(`document.querySelector("[data-viewer=close]").click()`);
  await goto("tab5");
  await ev(`document.querySelector(".ba-cols:not([hidden]) .expand-both").click()`);
  st = await ev(VIEWER);
  record("desktop.layout_choice_survives_reload", st.layout === "stack", `layout after reload=${st.layout}`);
  await ev(`document.querySelector("[data-viewer=close]").click(); localStorage.removeItem('ga-viewer-layout')`);

  await goto("tab1");
  const wide = await ev(`(() => { const cells = [...document.querySelectorAll("table.stack td")]; const label = document.querySelector("table.stack .cell-label");
    return { cells: cells.length, tableCell: cells.every((c) => getComputedStyle(c).display === "table-cell"), labelHidden: getComputedStyle(label).display === "none" }; })()`);
  record("desktop.tables_stay_tables_on_wide_screens", wide.cells > 0 && wide.tableCell && wide.labelHidden, JSON.stringify(wide));

  // ============================================================ (3) Thai
  const GLYPHS = `(() => {
    const draw = (s) => { const c = document.createElement("canvas"); c.width = 240; c.height = 40; const x = c.getContext("2d"); x.font = "24px " + getComputedStyle(document.body).fontFamily; x.fillText(s, 4, 28); return { px: x.getImageData(0, 0, 240, 40).data, w: x.measureText(s).width }; };
    const thai = draw("\\u0e01\\u0e02\\u0e04\\u0e07\\u0e08\\u0e09"), tofu = draw("\\uFFFF\\uFFFF\\uFFFF\\uFFFF\\uFFFF\\uFFFF");
    let ink = 0, diff = 0;
    for (let i = 0; i < thai.px.length; i += 4) { if (thai.px[i + 3] > 0) ink++; if (thai.px[i] !== tofu.px[i] || thai.px[i + 3] !== tofu.px[i + 3]) diff++; }
    const web = ["IBM Plex Sans Thai", "Noto Serif Thai"].map((f) => f + ":" + document.fonts.check('16px "' + f + '"')).join(",");
    return { ink, diff, thaiWidth: thai.w, tofuWidth: tofu.w, webFonts: web, lang: document.documentElement.lang };
  })()`;
  const TABS_FIT = `[...document.querySelectorAll(".tab")].filter((t) => t.scrollWidth > t.clientWidth + 1).map((t) => t.textContent.trim())`;
  for (const kind of ["desktop", "phone"]) {
    await emulate(kind);
    await goto("tab5", { lang: "th" });
    await ev(`document.querySelectorAll("details").forEach((d) => { d.open = true; })`);
    await sleep(200);
    const g = await ev(GLYPHS);
    record(`thai.glyphs_render_${kind}`, g.lang === "th" && g.ink > 50 && g.diff > 200 && Math.abs(g.thaiWidth - g.tofuWidth) > 0.5, `ink=${g.ink} differs-from-tofu=${g.diff} width ${g.thaiWidth.toFixed(1)} vs tofu ${g.tofuWidth.toFixed(1)}; web fonts loaded: ${g.webFonts}`);
    await screenshot(`thai-${kind}-tab5-data-dictionary`, "#CMP-DB .dictionary");
    await screenshot(`thai-${kind}-tab5-files-list`, "#CMP-DB .files");
    for (const page of PAGES) {
      await goto(page, { lang: "th" });
      await ev(`document.querySelectorAll("details").forEach((d) => { d.open = true; })`);
      const o = await ev(overflowJs());
      const clipped = await ev(TABS_FIT);
      record(`thai.no_overflow_${kind}.${page}`, (kind === "phone" ? o.sw === o.vw : o.sw <= o.vw) && o.bad.length === 0 && clipped.length === 0, `scrollWidth ${o.sw} vs ${o.vw}; offenders: ${o.bad.join(" | ") || "none"}; clipped tabs: ${clipped.join(",") || "none"}`);
    }
    await goto("tab4", { lang: "th" });
    await screenshot(`thai-${kind}-tab4-touchpoints`, ".uc .grid3");
    await goto("tab5", { lang: "th" });
    await screenshot(`thai-${kind}-tab5-full`);
  }
}

try {
  await main();
} catch (error) {
  record("harness.error", false, error.stack || error.message);
}
results._shots = shotPaths;
console.log("RESULT:" + JSON.stringify(results));
cleanup(0);
