"use strict";

// ---------- arm page ----------
function focusTrade(trade) {
  focusCandles(trade);
  document.querySelectorAll("#trades .stocklink, #tradebars .tbar").forEach((l) => l.classList.toggle("on", isFocused(l.dataset.symbol, l.dataset.buy)));
}
function showStock(symbol, buyIso, sellIso) {
  document.getElementById("symbol").value = symbol;
  document.getElementById("candles").closest(".card").scrollIntoView({ block: "nearest" });
  focusTrade(buyIso ? { symbol, from: isoSeconds(buyIso), to: isoSeconds(sellIso) } : null);
  syncPriceLines();
  if (focused) loadCandlesForTrade(focused);
  else loadCandles();
}
// cols: [key, label, align (1 left, "wrap"), format(value, row), tip (default: glossary's for key)]
function table(id, cols, rows, empty) {
  setHtml(document.getElementById(id), !rows.length ? `<tr><td class="l muted">${empty}</td></tr>` :
    `<thead><tr>${cols.map(([k, label, l, , tip]) => `<th class="${l ? "l" : ""}"${tipAttr(tip ?? window.TIPS[k])}>${label}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr>${cols.map(([k, , l, f]) => `<td class="${l ? (l === "wrap" ? "wrap" : "l") : ""}">${f ? f(r[k], r) : esc(r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`);
}

// stock picker: what the bot holds first, then what it has traded, then the rest
let universe = [];  // config order
function groupSymbols(pos, trades) {
  const sel = document.getElementById("symbol");
  const held = [...new Set(pos.map((p) => p.symbol))].filter((s) => universe.includes(s));
  const traded = [...new Set(trades.map((t) => t.symbol))].filter((s) => universe.includes(s) && !held.includes(s));
  const key = `${held}|${traded}`;
  if (sel.dataset.groups === key || document.activeElement === sel) return;  // open list: leave it alone
  sel.dataset.groups = key;
  const value = sel.value;
  const group = (label, symbols) => (symbols.length ? `<optgroup label="${label}">${symbols.map((s) => `<option>${esc(s)}</option>`).join("")}</optgroup>` : "");
  sel.innerHTML = group("Holding now", held) + group("Traded before", traded)
    + group(held.length || traded.length ? "Not traded yet" : "Stocks", universe.filter((s) => !held.includes(s) && !traded.includes(s)));
  sel.value = value;
}
// where the price sits between stop and target; the fill runs from entry to now
function rangeMeter(p) {
  const { stop_loss: stop, profit_target: target, entry_price: entry, price } = p;
  if (stop === null || target === null || stop === undefined || target === undefined || target <= stop) return `${fmt(stop)} → ${fmt(target)}`;
  const lo = Math.min(stop, entry, price), hi = Math.max(target, entry, price);
  const at = (v) => ((v - lo) / (hi - lo)) * 100, left = (v) => `left:${at(v).toFixed(1)}%`;
  return `<span class="range" data-tip="Sells at ${fmt(stop)} to cap the loss, or at ${fmt(target)} to take profit.\nBought at ${fmt(entry)}, now ${fmt(price)}."><span>${fmt(stop)}</span><span class="track">`
    + `<i class="gain ${cls(price - entry)}" style="${left(Math.min(entry, price))};width:${Math.abs(at(price) - at(entry)).toFixed(1)}%"></i>`
    + `<i class="mark stop" style="${left(stop)}"></i><i class="mark target" style="${left(target)}"></i><i class="mark entry" style="${left(entry)}"></i>`
    + `<i class="now" style="${left(price)}"></i></span><span>${fmt(target)}</span></span>`;
}
const heldFor = (h) => (h < 1 ? `${Math.round(h * 60)}m` : h < 48 ? `${fmt(h, 1)}h` : `${fmt(h / 24, 1)}d`);
// closed trades as bars, oldest left; trades come newest first
function drawTradeBars(trades) {
  const el = document.getElementById("tradebars");
  el.closest(".card").hidden = !trades.length;
  const cap = Math.max(1e-9, ...trades.map((t) => Math.abs(t.pnl)));
  setHtml(el, [...trades].reverse().map((t) => `<button class="tbar ${t.pnl >= 0 ? "win" : "loss"}${isFocused(t.symbol, t.entry_ts) ? " on" : ""}"`
    + ` data-symbol="${esc(t.symbol)}" data-buy="${esc(t.entry_ts)}" data-sell="${esc(t.exit_ts)}"${inUniverse(t.symbol) ? "" : " disabled"}`
    + ` data-tip="${esc(t.symbol)} · sold ${etTime(t.exit_ts)}\n${money(t.pnl, 2)}${t.r_multiple === null ? "" : ` (${signed(t.r_multiple)}R)`} · ${esc(t.exit_reason)}">`
    + `<i style="height:${((Math.abs(t.pnl) / cap) * 50).toFixed(1)}%"></i></button>`).join(""));
}
// decisions: one line each. Rows are kept across polls (keyed by id), so an open one stays open with its text
let decisionFilter = "all", lastDecisions = [];
// all: the latest 50. acted: the ones that led to a trade, however far back (queries.decisions)
const decisionsPath = () => `/api/arm/${encodeURIComponent(window.ARM)}/decisions${decisionFilter === "acted" ? "?acted=1" : ""}`;
const SIGNAL = { buy_to_enter: ["buy", "BUY"], close_position: ["sell", "SELL"], hold: ["hold", "hold"] };  // [css class, word]
// agent/schema.py SIGNALS may grow before this map does: show the raw signal, not a blank pane
const signalOf = (s) => SIGNAL[s] ?? ["other", s];
function drawDecisions() {
  const box = document.getElementById("decisions");
  // d.actions: each stock it traded or commented on, trades first. A daemon started before that field existed sends
  // d.summary, one string, instead: no chips and no per-stock list then
  const trades = (d) => (d.actions || []).filter((a) => a.signal !== "hold");
  const shown = decisionFilter === "acted" ? lastDecisions.filter((d) => !d.actions || trades(d).length) : lastDecisions;
  const key = `${decisionFilter}:${shown.map((d) => d.id)}`;
  if (box.dataset.key === key) return;  // a decision never changes once written
  box.dataset.key = key;
  if (!shown.length) { box.innerHTML = `<p class="empty">${decisionFilter === "acted" ? "No trades yet." : "No decisions yet."}</p>`; return; }
  const have = new Map([...box.querySelectorAll("details")].map((el) => [el.dataset.id, el]));
  const sig = (a, symbol = "") => { const [kind, word] = signalOf(a.signal);
    return `<span class="sig ${kind}">${esc(word)}${symbol && " " + esc(symbol)}</span>`; };
  const nodes = [];
  let day = "";
  for (const d of shown) {
    const t = new Date(d.slot_ts), today = etLongDayFmt.format(t);
    if (today !== day) nodes.push(Object.assign(document.createElement("div"), { className: "dday", textContent: day = today }));
    let el = have.get(String(d.id));
    if (!el) {
      el = document.createElement("details");
      el.dataset.id = d.id;
      const chips = d.status !== "ok" ? `<span class="sig err">${esc(d.status === "llm_failed" ? "AI call failed" : d.status)}</span>`
        : !d.actions ? "" : trades(d).map((a) => sig(a, a.symbol)).join("") || sig({ signal: "hold" });
      const line = !d.actions ? d.summary
        : d.actions.map((a) => `${a.symbol} ${signalOf(a.signal)[1].toLowerCase()}${a.why && ": " + a.why}`).join(" · ") || "hold all";
      const meta = [d.reason === "scheduled" ? "" : esc(d.reason), d.cost > 0 ? "$" + fmt(d.cost, 3) : ""].filter(Boolean).join(" · ");
      el.innerHTML = `<summary><span class="dtime">${etClock(t)}</span>${chips}<span class="dsum">${esc(line)}</span><span class="dmeta">${meta}</span></summary>`
        + `<div class="detail">${d.actions?.length ? `<table class="dnotes"><tbody>${d.actions.map((a) =>
            `<tr><td class="l"><b>${esc(a.symbol)}</b></td><td class="l">${sig(a)}</td><td class="wrap">${esc(a.why)}</td></tr>`).join("")}</tbody></table>` : ""}`
        + `<div class="full muted">loading the prompt and reply…</div></div>`;
    }
    nodes.push(el);
  }
  box.replaceChildren(...nodes);
}
// prompt and reply as markdown. Text is the model's and the news': HTML in it shows as text, not tags (the prompt's
// "<query>" would vanish), and still goes through DOMPurify. An image shows its alt text: opening a decision fetches
// nothing a news story names. No CDN: plain, the original text, not the markdown made from it
let marker;
function md(src, plain = src) {
  if (!window.marked || !window.DOMPurify) return `<pre>${esc(plain)}</pre>`;
  marker ??= new marked.Marked({ renderer: { html: ({ text }) => esc(text), image: ({ text }) => esc(text) } });
  return `<div class="md">${DOMPurify.sanitize(marker.parse(src))}</div>`;
}
const mdCell = (v) => String(v ?? "").replace(/[\\`*_[\]<>|#~&]/g, "\\$&").replace(/\n/g, " ");
const isRow = (v) => v && typeof v === "object" && !Array.isArray(v) && Object.values(v).every((x) => x === null || typeof x !== "object");
function mdTable(rows) {  // rows: [label, flat object]; columns: every key, first-seen order
  const head = ["", ...new Set(rows.flatMap(([, o]) => Object.keys(o)))], cols = head.slice(1);
  return [head, head.map(() => "---"), ...rows.map(([k, o]) => [k, ...cols.map((c) => o[c])])]
    .map((r) => `| ${r.map(mdCell).join(" | ")} |`).join("\n");
}
// one reply: a JSON object (stocks, chart or search call) as tables; anything else (bad JSON, HTTP error) verbatim
function replyMd(part) {
  let obj;
  try { obj = JSON.parse(part); } catch { obj = null; }
  if (!obj || typeof obj !== "object") {  // fence longer than any ~ run inside, or that run would end it
    const fence = "~".repeat(Math.max(3, ...(part.match(/~+/g) ?? []).map((t) => t.length)) + 1);
    return `${fence}\n${part}\n${fence}`;
  }
  const rows = [], rest = [];
  for (const [k, v] of Object.entries(obj)) {
    if (isRow(v)) rows.push([k, v]);
    else if (Array.isArray(v) && v.length && v.every(isRow)) rest.push(`**${mdCell(k)}**\n\n${mdTable(v.map((o, i) => [i + 1, o]))}`);
    else rest.push(`**${mdCell(k)}:** ${mdCell(v && typeof v === "object" ? JSON.stringify(v) : v)}`);
  }
  return [rows.length ? mdTable(rows) : "", ...rest].filter(Boolean).join("\n\n");
}
async function openDecision(el) {
  if (!el.open || el.dataset.loaded) return;
  el.dataset.loaded = "1";
  const full = el.querySelector(".full");
  const d = await get(`/api/decision/${el.dataset.id}`).catch((e) => e);
  if (d instanceof Error) {
    delete el.dataset.loaded;
    full.textContent = d.message === NOT_MIRRORED ? "Not mirrored: open it on the PC dashboard." : "Could not load. Close and open again to retry.";
    return;
  }
  // chart-tool pictures the bot saw, re-drawn (agent/chart_tool.py seen)
  const pics = d.charts.map((c) =>
    `<img alt="${esc(c.symbol)} chart" style="max-width:100%" src="${esc(url("/api/chart?" + new URLSearchParams(c)))}">`);
  full.classList.remove("muted");
  const replies = d.replies ?? [d.raw];  // a daemon started before d.replies existed: one block, unsplit
  full.innerHTML = `<h2>Reply <span class="muted">what the model answered, every reply in order</span></h2>${md(replies.map(replyMd).join("\n\n---\n\n"), d.raw)}`
    + `<h2>Prompt <span class="muted">everything it was shown</span></h2>${pics.join("")}${md(d.prompt)}`;
}
// this bot's account against just holding the benchmark
async function loadArmEquity() {
  const bench = window.SERIES.find(isBench), pair = [bench, window.ARM].filter(Boolean);
  const eq = await get(`/api/equity?mode=usd&arms=${encodeURIComponent(pair.join(","))}`);  // only the pair
  const grid = sessionGrid(eq);
  const lines = sessionDatasets(grid, eq, pair);
  if (chartEmpty("armequity", !lines.length)) return;
  draw("armequity", "line", { datasets: lines }, sessionOptions(grid, dollars));
  drawLegend("armlegend", lines.map((ds) => legendItem(ds.key, dollars(lastValue(ds)))).reverse());
}
let symbolPicked = false;  // first load only: open the chart on a stock this bot holds or last traded, not the universe's first
async function loadArm(st) {
  const arm = encodeURIComponent(window.ARM);
  const me = st.arms.find((a) => a.arm === window.ARM) || {};
  const sized = me.variant === "improved";  // only improved arms send conviction
  const [pos, trades, decs, rej, conv] = await Promise.all([get(`/api/arm/${arm}/positions`), get(`/api/arm/${arm}/trades`),
    get(decisionsPath()), get(`/api/arm/${arm}/rejections`), sized ? get(`/api/arm/${arm}/conviction`) : [],
    refreshRendered([".kpis"]), loadArmEquity().catch((e) => console.error(e))]);
  setHtml(document.getElementById("armstate"),
    (me.model && me.model !== "-" ? `<span class="chip">model <b>${esc(me.model)}</b></span>` : `<span class="chip">no AI model</span>`)
    + `<span class="chip">setup <b>${esc(me.variant ?? "–")}</b></span>`
    + `<span class="chip">last decision <b>${me.last_decision ? etTime(me.last_decision) : "–"}</b></span>`
    + (me.paused || st.paused_all ? `<span class="chip"><span class="dot" style="background:${css("--warning")}"></span><b>paused</b></span>` : ""));
  const btn = document.getElementById("pausebtn");
  if (btn) {  // no button on the read-only mirror
    btn.textContent = me.paused ? "Resume" : "Pause";
    btn.classList.toggle("warn", !!me.paused);
    btn.onclick = async () => { await fetch(url(`/api/${me.paused ? "resume" : "pause"}/${arm}`), { method: "POST" }); loadArm(await loadStatus()); };
  }

  openPositions = pos;
  groupSymbols(pos, trades);
  if (!symbolPicked) {
    symbolPicked = true;
    const first = [...pos, ...trades].map((r) => r.symbol).find((s) => universe.includes(s));
    if (first) document.getElementById("symbol").value = first;
    loadCandles();
  } else syncPriceLines();

  const pnl = (v) => `<span class="${cls(v)}">${signed(v)}</span>`;
  table("positions", [["symbol", "stock", 1, (v) => stockLink(v)], ["qty", "shares", 0, (v) => fmt(v, 3)],
    ["worth", "worth", 0, (v, r) => dollars(r.qty * r.price)], ["entry_price", "entry", 0, (v) => fmt(v)],
    ["price", "price now", 0, (v) => fmt(v)],
    ["unrealized", "unrealized", 0, (v, r) => `${pnl(v)} <span class="muted small">${signed((r.price / r.entry_price - 1) * 100, 1)}%</span>`],
    ["range", "stop → target", 1, (v, r) => rangeMeter(r)], ["invalidation", "proves it wrong", "wrap"]], pos, "No open positions.");
  drawTradeBars(trades);
  table("trades", [["symbol", "stock", 1, (v, r) => stockLink(v, r.entry_ts, r.exit_ts)], ["entry_ts", "bought", 1, etTime], ["exit_ts", "sold", 1, etTime],
    ["entry_price", "in", 0, (v) => fmt(v)], ["exit_price", "out", 0, (v) => fmt(v)], ["hold_hours", "held", 0, heldFor],
    ["pnl", "P&L", 0, pnl, "Profit or loss of this trade, in dollars."], ["r_multiple", "R", 0, (v) => signed(v, 2)],
    ...(sized ? [["conviction", "conviction", 0, (v) => fmt(v)]] : []),
    ["exit_reason", "exit", 1, (v, r) => esc(v) + (r.replayed ? ' <span class="chip" data-tip="Filled while catching up after the PC was off">replayed</span>' : "")],
    ["justification", "reason", "wrap"]], trades, "No closed trades yet.");
  document.getElementById("convcard").hidden = !sized;
  if (sized) table("conviction", [["conviction", "conviction at entry", 1], ["trades", "trades"], ["win_rate", "win rate %", 0, (v) => fmt(v, 0)],
    ["avg_r", "avg R", 0, (v) => signed(v, 2)]], conv, "No closed trades yet.");
  // which rules did the blocking, most often first, each in plain words (r.meaning: glossary.RULES)
  const rules = new Map();
  for (const r of rej) rules.set(r.rule, { n: (rules.get(r.rule)?.n || 0) + 1, meaning: r.meaning });
  setHtml(document.getElementById("rejectwhy"), [...rules].sort((a, b) => b[1].n - a[1].n).map(([rule, { n, meaning }]) =>
    `<div class="why"><b>${esc(rule)}</b><span class="count">×${n}</span><span>${esc(meaning || "")}</span></div>`).join(""));
  table("rejections", [["ts", "time", 1, etTime], ["symbol", "stock", 1], ["rule", "rule broken", 1, (v, r) => `<span${tipAttr(r.meaning)}>${esc(v)}</span>`],
    ["detail", "the order", "wrap"]], rej, "Nothing rejected.");
  // decisions and rejections come capped (50 / 100), so only uncapped lists get a count
  document.getElementById("n-positions").textContent = pos.length;
  document.getElementById("n-trades").textContent = trades.length;
  lastDecisions = decs;
  drawDecisions();
}
// tabs on the bot page; the open tab lives in the URL hash so reload and back keep it
function showTab(name) {
  const tabs = [...document.querySelectorAll(".subtabs button")];
  if (!tabs.some((b) => b.dataset.tab === name)) name = tabs[0].dataset.tab;
  tabs.forEach((b) => { b.classList.toggle("on", b.dataset.tab === name); b.setAttribute("aria-selected", b.dataset.tab === name); });
  document.querySelectorAll(".tabpane").forEach((p) => { p.hidden = !p.dataset.pane.split(" ").includes(name); });
  focusTrade(null);  // only a click on a closed trade focuses; that stays on this tab
  if (candleChart && !document.getElementById("candles").closest(".tabpane").hidden) fitCandles();
}
function initArm() {
  universe = [...document.getElementById("symbol").options].map((o) => o.value);
  document.querySelectorAll(".subtabs button").forEach((b) => b.onclick = () => { location.hash = b.dataset.tab; });
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));
  document.getElementById("symbol").onchange = () => { focusTrade(null); syncPriceLines(); loadCandles(); };
  document.getElementById("tf").onchange = loadCandles;
  decisionFilter = segmented("dfilter", async (v) => {
    decisionFilter = v;
    const fresh = await get(decisionsPath());
    if (v !== decisionFilter) return;  // clicked again while in flight
    lastDecisions = fresh;
    drawDecisions();
  }, true) || decisionFilter;
  // delegated: polling re-renders the rows
  ["positions", "trades", "tradebars"].forEach((id) => document.getElementById(id).addEventListener("click", (e) => {
    const link = e.target.closest(".stocklink, .tbar");
    if (!link) return;
    showStock(link.dataset.symbol, link.dataset.buy, link.dataset.sell);
    // a bar's trade may be scrolled out of the table: bring its row up, inside the table's own scroll only
    const row = document.querySelector("#trades tr:has(.stocklink.on)"), wrap = row?.closest(".scrolly");
    if (id === "tradebars" && wrap) wrap.scrollTop = row.offsetTop - wrap.clientHeight / 2;
  }));
  document.getElementById("decisions").addEventListener("toggle", (e) => openDecision(e.target), true);  // toggle does not bubble
  return loadArm;
}
