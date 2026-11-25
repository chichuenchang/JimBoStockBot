// StockBot dashboard. Colors follow the entity: arm i -> categorical slot i (fixed order);
// the benchmark is a neutral dashed line, never a categorical slot.
"use strict";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isBench = (s) => s.startsWith("benchmark");
const ANALYST = "analyst";  // usage rows with no arm (queries.ANALYST)
const gone = [];  // arms in the DB but not in config: slots after config arms, in first-seen order
function colorOf(series) {
  if (isBench(series) || series === ANALYST) return css("--bench");  // not a bot: neutral
  const arms = window.SERIES.filter((s) => !isBench(s));
  let i = arms.indexOf(series);
  if (i < 0) i = arms.length + (gone.includes(series) ? gone.indexOf(series) : gone.push(series) - 1);
  return css(`--series-${(i % 8) + 1}`);
}
const nameOf = (s) => (s === ANALYST ? "market report" : window.NAMES[s] || s);
const stateColor = (state) => css(state === "ok" ? "--good" : state === "warn" ? "--warning" : "--critical");
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const kTok = (n) => (n >= 1e6 ? fmt(n / 1e6, 1) + "M" : n >= 1e3 ? fmt(n / 1e3, 0) + "k" : String(n));
const signed = (v, d = 2) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const tri = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
const dollars = (v) => "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
// signed dollars, sign before the $: +$82, -$1,204
const money = (v, d = 0) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : v < 0 ? "-" : "") + "$"
  + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const ET = "America/New_York";
const etTime = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: ET,
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
// one formatter each, reused: toLocaleString builds a new one per call, too slow for a few thousand chart points
const etClockFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const etDayFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, month: "short", day: "numeric" });
const etLongDayFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", month: "short", day: "numeric" });
const etClock = (t) => etClockFmt.format(new Date(t));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const get = (url) => fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${url}: HTTP ${r.status}`))));
const tipAttr = (text) => (text ? ` data-tip="${esc(text)}"` : "");
const pref = (key, fallback) => { try { return localStorage.getItem("jbsb." + key) ?? fallback; } catch (e) { return fallback; } };
const setPref = (key, value) => { try { localStorage.setItem("jbsb." + key, value); } catch (e) { /* private window: this page only */ } };
const quantile = (xs, q) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0; };

const paintSwatches = (root) => root.querySelectorAll(".swatch[data-series]").forEach((el) => {
  el.style.background = colorOf(el.dataset.series); });
paintSwatches(document);

// segmented buttons (data-<key>="value"): a click marks that one on and hands its value to pick.
// remember: the choice outlives the page (localStorage) and is returned so the caller starts from it
function segmented(key, pick, remember = false) {
  const bs = [...document.querySelectorAll(`button[data-${key}]`)];
  const mark = (b) => bs.forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
  bs.forEach((b) => b.onclick = () => { mark(b); if (remember) setPref(key, b.dataset[key]); pick(b.dataset[key]); });
  const saved = remember && bs.find((b) => b.dataset[key] === pref(key, null));
  mark(saved || bs.find((b) => b.classList.contains("on")));
  return saved ? saved.dataset[key] : null;
}

// light / dark: base.html applies the saved choice before paint; charts read colors once, so reload
document.getElementById("theme").onclick = () => {
  const next = css("color-scheme") === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", next); } catch (e) { /* private window: this page only */ }
  document.documentElement.dataset.theme = next;
  location.reload();
};

// ---------- help bubbles: [data-tip] ----------
// Not title=: that waits a second, cannot be styled, and never shows on a touch screen. Shows on hover, keyboard focus and tap.
(function initTips() {
  const tip = Object.assign(document.createElement("div"), { id: "tip", hidden: true });
  tip.setAttribute("role", "tooltip");
  document.body.append(tip);
  let owner = null;
  const hide = () => { owner = null; tip.hidden = true; };
  function show(el) {
    owner = el;
    tip.textContent = el.dataset.tip;  // text, never HTML: tips carry model and stock names
    tip.hidden = false;
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect(), gap = 8;
    tip.style.top = `${r.top - t.height - gap >= 4 ? r.top - t.height - gap : r.bottom + gap}px`;  // above, else below
    tip.style.left = `${Math.max(4, Math.min(r.left + r.width / 2 - t.width / 2, innerWidth - t.width - 4))}px`;
  }
  const over = (e) => {
    const el = e.target.closest?.("[data-tip]");
    if (!el || !el.dataset.tip) hide();
    else if (el !== owner) show(el);
  };
  document.addEventListener("pointerover", over);
  document.addEventListener("focusin", over);
  document.addEventListener("focusout", hide);
  document.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse") hide(); });  // a click is not a question
  document.documentElement.addEventListener("pointerleave", hide);
  addEventListener("scroll", hide, { capture: true, passive: true });
  setInterval(() => { if (owner && !owner.isConnected) hide(); }, 1000);  // a poll replaced the node under the pointer
})();

// polled content: change only the nodes that differ, so focus, a press in progress and text selection survive the latest data
function patchChildren(el, fresh) {
  const had = [...el.childNodes], want = [...fresh.childNodes];
  want.forEach((n, i) => {
    const o = had[i];
    if (!o) el.append(n);
    else if (o.isEqualNode(n)) return;
    else if (o.cloneNode(false).isEqualNode(n.cloneNode(false))) patchChildren(o, n);
    else o.replaceWith(n);
  });
  had.slice(want.length).forEach((o) => o.remove());
}
function setHtml(el, html) {
  const fresh = el.cloneNode(false);  // a table's rows only parse inside a table
  fresh.innerHTML = html;
  patchChildren(el, fresh);
}

// server-rendered stats (tiles, stats tables, KPIs): re-fetch this page and swap them in, so they keep pace with the charts.
// dress[selector](fresh): finish a fresh node the way the one on screen was (layout, sort), so equal data patches nothing
async function refreshRendered(selectors, dress = {}) {
  const doc = new DOMParser().parseFromString(await fetch(location.pathname).then((r) => r.text()), "text/html");
  for (const s of selectors) {
    const el = document.querySelector(s), fresh = doc.querySelector(s);
    if (!el || !fresh) continue;
    paintSwatches(fresh);  // before comparing: a painted swatch must equal the one on screen
    dress[s]?.(fresh, el);
    patchChildren(el, fresh);
  }
}
