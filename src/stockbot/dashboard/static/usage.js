"use strict";

// ---------- usage page: tokens per bot (queries.usage) ----------
let usageSpan = "today";
// running total per bot today. Bots call at their own moments: put every total on every moment, so one hover reads them all
function drawTokensToday(u, drawn) {
  const ts = [...new Set(drawn.flatMap((b) => u.series[b].map(([x]) => x)))].sort((a, b) => a - b);
  const o = timeLinear(baseOptions(kTok), ts[0] ?? Date.now() - 36e5, ts.at(-1) ?? Date.now());
  draw("tokens", "line", { datasets: drawn.map((b) => ({ ...lineStyle(b), stepped: true,
    data: alignTo(ts, u.series[b], true).map((y, i) => ({ x: ts[i], y })) })) }, o);
}
// one stacked bar per ET day, a segment per bot; tooltip: the hovered segment's day totals (queries._totals)
function drawTokensDaily(u, drawn, cost) {
  const tok = (t) => t.input + t.output;
  // null, not 0, on a bot's idle day: no zero-height segment to hover, so tooltips always have totals
  const ds = drawn.map((b) => ({
    label: nameOf(b), bot: b, data: u.daily.map((d) => d.bots[b] ? tok(d.bots[b]) : null), ...barStyle(b) }));
  const o = baseOptions(kTok);
  o.scales.x.stacked = o.scales.y.stacked = true;
  o.scales.x.grid.display = false;
  o.interaction = { mode: "point", intersect: true };
  Object.assign(o.plugins.tooltip, { itemSort: undefined, callbacks: {
    title: (items) => items.length ? new Date(u.daily[items[0].dataIndex].day + "T12:00:00Z").toLocaleDateString("en-US",
      { timeZone: "UTC", weekday: "short", year: "numeric", month: "short", day: "numeric" }) : "",
    label: (c) => { const t = u.daily[c.dataIndex].bots[c.dataset.bot];
      return [` ${kTok(tok(t))} tokens  ${c.dataset.label} · ${t.calls} calls · ${cost(t, c.dataset.bot)}`,
              `   ${kTok(t.input)} in (${kTok(t.cached)} cached) / ${kTok(t.output)} out`]; } } });
  draw("tokens", "bar", { labels: u.daily.map((d) => `${+d.day.slice(5, 7)}/${+d.day.slice(8)}`), datasets: ds }, o);
}
function drawUsageKpis(u, st) {
  const today = (k) => u.bots.reduce((a, b) => a + b.today[k], 0);
  const billed = u.bots.filter((b) => !b.flat).reduce((a, b) => a + b.month.cost, 0);
  const cap = st?.budget._total.cap, input = today("input");
  const tiles = [
    ["Tokens today", kTok(input + today("output")), `${today("calls")} calls · ${kTok(input)} in / ${kTok(today("output"))} out`,
     "A token is about three quarters of a word. In = what the bots read (prices, news, charts); out = what they wrote back."],
    ["Read from cache today", input ? `${fmt((today("cached") / input) * 100, 0)}%` : "–", "of input tokens, billed at a fraction",
     "The start of every prompt repeats. The provider remembers it and charges far less for that part."],
    ["Billed this month", `$${fmt(billed)}`, cap ? `${meter(billed / cap, st.budget._total.state)} of $${fmt(cap, 0)} budget` : "pay-per-token models",
     "Dollars for pay-per-token models. Flat-plan models cost a fixed fee and count against a quota instead."],
    ...Object.entries(st?.quota || {}).map(([name, q]) => [`${name} plan quota`, `${fmt(Math.max(...q.windows.map((w) => w.used_ratio)) * 100, 0)}%`,
      q.windows.map((w) => `${meter(w.used_ratio, q.state)} ${esc(w.window)} ${fmt(w.used_ratio * 100, 0)}%`).join(" &nbsp; "),
      window.TIPS.flat_plan]),
  ];
  setHtml(document.getElementById("usagekpis"), tiles.map(([label, value, sub, tip]) =>
    `<div class="stat"${tipAttr(tip)}><div class="lbl">${esc(label)}</div><div class="val">${value}</div><div class="sub">${sub}</div></div>`).join(""));
}
async function loadUsage(st = lastStatus) {
  const span = usageSpan;
  const u = await get(`/api/usage?span=${span}`);
  if (span !== usageSpan) return;  // span clicked while in flight: the click's own load draws
  const bots = u.bots.map((b) => b.bot), flat = Object.fromEntries(u.bots.map((b) => [b.bot, b.flat]));
  const cost = (t, b) => (flat[b] ? "plan" : "$" + fmt(t.cost, 3));
  drawUsageKpis(u, st);
  document.getElementById("tokens-note").textContent = span === "today" ? "running total today, New York time" : "per New York day · hover a bar for detail";
  const drawn = bots.filter((b) => (span === "today" ? u.series[b]?.length : u.daily.some((d) => d.bots[b])));
  if (!chartEmpty("tokens", !drawn.length, span === "today" ? "No AI calls yet today. The bots decide only while the market is open."
                                                             : "No AI calls in the last 7 days.")) {
    if (span === "today") drawTokensToday(u, drawn);
    else drawTokensDaily(u, drawn, cost);
  }
  drawLegend("tokenslegend", drawn.map((b) => legendItem(b)));

  const cols = ["calls", "input", "cached", "output", "cost"];
  const tip = { calls: "Model calls", input: "Prompt tokens sent", cached: "Part of input the provider served from cache (cheaper)",
                output: "Tokens the model wrote back", cost: "Dollars billed; $0 on a flat plan" };
  const cell = (t, k, b) => k === "cost" ? `<span class="${b.flat ? "muted" : ""}">${cost(t, b.bot)}</span>` : k === "calls" ? t[k] : kTok(t[k]);
  setHtml(document.getElementById("usage"),
    `<thead><tr class="groups"><th class="l"></th><th class="l"></th><th colspan="5" class="l gstart">Today</th><th colspan="5" class="l gstart">This month</th></tr>` +
    `<tr><th class="l">bot</th><th class="l">model</th>${["today", "month"].map(() => cols.map((k, i) =>
      `<th data-tip="${tip[k]}" class="${i ? "" : "gstart"}">${k}</th>`).join("")).join("")}</tr></thead><tbody>` +
    u.bots.map((b) => `<tr><td class="l"><span class="swatch" style="background:${colorOf(b.bot)}"></span>${esc(nameOf(b.bot))}</td>` +
      `<td class="l">${esc(b.model)}</td>${["today", "month"].map((p) => cols.map((k, i) =>
        `<td class="${i ? "" : "gstart"}">${cell(b[p], k, b)}</td>`).join("")).join("")}</tr>`).join("") + `</tbody>`);
}
function initUsage() {
  segmented("span", (v) => { usageSpan = v; loadUsage(); });
  return loadUsage;
}
