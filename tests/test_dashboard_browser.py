"""Dashboard in real Chrome: clicks, focus, frames and layout, which only a browser has."""
import hashlib
import socket
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
import uvicorn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from stockbot.clock import RealClock, et_at, et_date
from stockbot.dashboard.app import create_app
from stockbot.data.base import TF_DURATION
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.models import BENCHMARK, Bar, ExitPlan, Fill, Position, Session
from tests.snapshot import CFG

ARM = "deepseek-baseline"


class FakeData:
    """Bars for any window, every day a full session: tests stay independent of the clock."""

    async def bars(self, symbol, tf, start, end):
        step = TF_DURATION[tf]
        ts = et_at(et_date(start), "00:00")
        out = []
        while ts < end:
            if ts >= start:
                out.append(Bar(symbol, ts, 100, 101, 99, 100, 1000))
            ts += step
        return out

    async def calendar(self, start: date, end: date):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return [Session(d, et_at(d, "00:00"), et_at(d + timedelta(days=1), "00:00")) for d in days]

    async def latest_prices(self, symbols):
        return {}


class Dashboard:
    """Live server on a seeded DB. Now-relative buys pick the timeframe: windows are app.py INTRADAY_SESSIONS."""

    def __init__(self, tmp_path):
        self.db = db = DB.open(tmp_path / "t.db")
        self.now = datetime.now(UTC)
        for a in (ARM, "kimi-improved", BENCHMARK):
            db.ensure_account(a, 10000)
            db.snapshot_equity(a, self.now - timedelta(days=1), 10000)
        self.closed("MSFT", timedelta(days=10))
        self.closed("NVDA", timedelta(hours=3))
        self.closed("AMZN", timedelta(days=100))
        self.closed("DELISTED", timedelta(days=10))
        self.position("TSLA", 100)
        self.position("GOOGL", 100)
        self.db.insert_usage("kimi-k3", "kimi-improved", "decision", self.now, 1000, 0, 100, 0.0)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        app = create_app(db, CFG, Budget(db, CFG, RealClock()), FakeData())
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.01)

    def closed(self, symbol, ago, hold=timedelta(hours=1)):
        """A closed trade bought `ago` before now, sold `hold` later or now."""
        buy = self.now - ago
        self.db.insert_fill(Fill(ARM, symbol, "buy", 1, 100, buy, "entry"), "why", 2.0)
        self.db.insert_fill(Fill(ARM, symbol, "sell", 1, 110, min(buy + hold, self.now), "target"))

    def position(self, symbol, price):
        acct = self.db.load_account(ARM)
        acct.positions[symbol] = Position(symbol, 1, 100, self.now - timedelta(days=1), ExitPlan(90, 120))
        self.db.save_account(acct)
        self.price(symbol, price)

    def price(self, symbol, price):
        self.db.kv_set(f"last_close:{symbol}", str(price))

    def stop(self):
        self.server.should_exit = True
        self.thread.join()


@pytest.fixture
def dash(tmp_path):
    d = Dashboard(tmp_path)
    yield d
    d.stop()


@pytest.fixture(scope="module")  # not session: sync_playwright runs an event loop, which breaks later asyncio.run
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome")
        except PlaywrightError as e:
            pytest.skip(f"installed Chrome needed: {e}")
        yield b
        b.close()


def open_page(browser, cache_dir, url, has_touch=False):
    """New page with CDN scripts cached on disk (tests run offline after the first), failing on any page error."""
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, has_touch=has_touch)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))

    def cdn(route):
        f = cache_dir / hashlib.sha256(route.request.url.encode()).hexdigest()
        if not f.exists():
            f.write_bytes(route.fetch().body())
        route.fulfill(body=f.read_bytes(), content_type="application/javascript")

    page.route("https://**/*", cdn)
    page.goto(url)
    return page


@pytest.fixture
def page_at(browser, dash, pytestconfig):
    pages = []

    def go(path, has_touch=False):
        pages.append(open_page(browser, pytestconfig.cache.mkdir("cdn"), dash.url + path, has_touch))
        return pages[-1]

    yield go
    errors = [e for p in pages for e in p.errors]
    for p in pages:
        p.context.close()
    assert not errors


CHART_READY = "document.querySelector('#trades .stocklink') && candleChart"
POLL_ARM = "loadStatus().then(loadArm)"
POLL_INDEX = "loadIndex()"
POLL_USAGE = "loadUsage()"
FILLINFO = "document.getElementById('fillinfo').textContent"


def link(page, table, symbol):
    return page.locator(f'#{table} .stocklink[data-symbol="{symbol}"]')


def cells(page, table, row_text):
    return page.locator(f"#{table} tr", has_text=row_text).first.locator("td").all_inner_texts()


def is_focused(locator):
    return locator.evaluate("e => e === document.activeElement")


def arm_page(page_at, tab, has_touch=False):
    page = page_at(f"/arm/{ARM}#{tab}", has_touch)
    page.wait_for_function(CHART_READY)
    return page


def assert_trade_framed(page, dash, ago, hold=timedelta(hours=1)):
    """About 80 bars or more, buy and sell (Dashboard.closed) both shown, trade in the middle: derived from the seed, not from app.js."""
    buy = dash.now - ago
    sell = min(buy + hold, dash.now)
    page.wait_for_function("""([buy, sell]) => {
      const r = candleChart.timeScale().getVisibleLogicalRange(), bars = candleSeries.data();
      const b = bars.findLastIndex((x) => x.time <= buy), s = bars.findLastIndex((x) => x.time <= sell);
      return r && b >= 0 && r.to - r.from >= 78 && r.from < b && s < r.to && Math.abs((r.from + r.to) / 2 - (b + s) / 2) < 2;
    }""", arg=[buy.timestamp(), sell.timestamp()], timeout=5000)


# ---------- live tables: latest data, and whatever the user is doing in them survives ----------
def test_price_update_shows_and_keeps_keyboard_focus(dash, page_at):
    page = arm_page(page_at, "positions")
    link(page, "positions", "GOOGL").focus()
    dash.price("GOOGL", 123.45)
    page.evaluate(POLL_ARM)
    assert "123.45" in cells(page, "positions", "GOOGL")
    assert is_focused(link(page, "positions", "GOOGL"))


def test_poll_mid_press_shows_latest_and_click_still_lands(dash, page_at):
    page = arm_page(page_at, "positions")
    link(page, "positions", "GOOGL").hover()
    page.mouse.down()
    dash.price("GOOGL", 123.45)
    page.evaluate(POLL_ARM)
    assert "123.45" in cells(page, "positions", "GOOGL")
    page.mouse.up()
    page.wait_for_function("document.getElementById('symbol').value === 'GOOGL'")


def test_new_closed_trade_appears_first(dash, page_at):
    page = arm_page(page_at, "trades")
    assert page.locator("#trades tbody tr").count() == 4
    dash.closed("QQQ", timedelta(seconds=1))
    page.evaluate(POLL_ARM)
    assert page.locator("#trades tbody tr").count() == 5
    assert page.locator("#trades tbody tr").first.locator("td").first.inner_text() == "QQQ"


def test_closed_position_rows_leave_down_to_empty_message(dash, page_at):
    page = arm_page(page_at, "positions")
    assert page.locator("#positions tbody tr").count() == 2
    acct = dash.db.load_account(ARM)
    del acct.positions["TSLA"]
    dash.db.save_account(acct)
    page.evaluate(POLL_ARM)
    assert page.locator("#positions tbody tr").all_inner_texts()[0].startswith("GOOGL")
    assert page.locator("#positions tbody tr").count() == 1
    acct.positions.clear()
    dash.db.save_account(acct)
    page.evaluate(POLL_ARM)
    assert page.locator("#positions th").count() == 0
    assert page.locator("#positions").inner_text() == "No open positions."


def test_empty_table_gets_header_and_rows_when_first_row_arrives(dash, page_at):
    acct = dash.db.load_account(ARM)
    acct.positions.clear()
    dash.db.save_account(acct)
    page = arm_page(page_at, "positions")
    assert page.locator("#positions").inner_text() == "No open positions."
    dash.position("NVDA", 100)
    page.evaluate(POLL_ARM)
    assert page.locator("#positions th").first.inner_text() == "stock"
    assert page.locator("#positions tbody tr").count() == 1
    assert link(page, "positions", "NVDA").count() == 1


@pytest.mark.parametrize("box", ["gainers", "leaderboard", "boards"])
def test_index_table_updates_and_keeps_focus_on_bot_link(dash, page_at, box):
    page = page_at("/")
    page.wait_for_function("document.querySelector('#gainers a')")
    bot = page.locator(f'#{box} a[href="/arm/{ARM}"]').first
    bot.focus()
    row = bot.locator("xpath=ancestor::tr")
    before = row.inner_text()
    acct = dash.db.load_account(ARM)
    acct.cash += 5000  # top rank before and after: the link's own cell does not move
    dash.db.save_account(acct)
    page.evaluate(POLL_INDEX)
    assert row.inner_text() != before
    assert is_focused(bot)


def test_leaderboard_swatch_stays_painted_after_poll(page_at):
    page = page_at("/")
    page.evaluate(POLL_INDEX)
    swatch = page.locator("#leaderboard .swatch").first
    assert swatch.evaluate("e => getComputedStyle(e).backgroundColor") != "rgba(0, 0, 0, 0)"


def test_heatmap_grows_a_column_for_a_new_stock(dash, page_at):
    page = page_at("/")
    page.wait_for_function("document.querySelector('#heatmap th')")
    assert page.locator("#heatmap th", has_text="QQQ").count() == 0
    dash.closed("QQQ", timedelta(seconds=1))
    page.evaluate(POLL_INDEX)
    assert page.locator("#heatmap th", has_text="QQQ").count() == 1


def test_usage_table_shows_new_calls(dash, page_at):
    page = page_at("/usage")
    page.wait_for_function("document.querySelector('#usage tbody')")
    row = page.locator("#usage tbody tr", has_text="kimi-improved")
    before = row.inner_text()
    dash.db.insert_usage("kimi-k3", "kimi-improved", "decision", dash.now, 5000, 0, 500, 0.0)
    page.evaluate(POLL_USAGE)
    assert row.inner_text() != before


# ---------- trade badges on the price chart ----------
def badge_pixels(page, when, token):
    """Exact-color pixels of a CSS token in the column of the bar holding `when`, above its high and below its low."""
    return page.evaluate("""async ([ts, token]) => {
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);  // the chart paints on a frame
      const bar = candleSeries.data().findLast((b) => b.time <= ts);
      const x = Math.round(candleChart.timeScale().timeToCoordinate(bar.time));
      const shot = candleChart.takeScreenshot();
      const px = shot.getContext("2d").getImageData(x, 0, 1, shot.height).data;
      const hex = css(token), rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      const count = (from, to) => {
        let n = 0;
        for (let y = Math.max(from, 0); y < to; y++) if (rgb.every((v, c) => px[y * 4 + c] === v)) n++;
        return n;
      };
      return { above: count(0, Math.round(candleSeries.priceToCoordinate(bar.high))),
               below: count(Math.round(candleSeries.priceToCoordinate(bar.low)), shot.height) };
    }""", [when.timestamp(), token])


def bar_x(page, when):
    return page.evaluate("""(ts) => { const bar = candleSeries.data().findLast((b) => b.time <= ts);
      return candleChart.timeScale().timeToCoordinate(bar.time); }""", when.timestamp())


def bar_point(page, when, dx=0):
    """Page coordinates over the bar holding `when`, for a mouse or touch event."""
    box = page.locator("#candles").bounding_box()
    return box["x"] + bar_x(page, when) + dx, box["y"] + 100


def page_at_msft_sell(dash, page_at, has_touch=False):
    """Chart centered on MSFT's closed trade, and when that trade sold."""
    page = arm_page(page_at, "trades", has_touch)
    link(page, "trades", "MSFT").click()
    assert_trade_framed(page, dash, timedelta(days=10))
    return page, dash.now - timedelta(days=10) + timedelta(hours=1)


@pytest.mark.parametrize("symbol,ago", [("MSFT", timedelta(days=10)), ("AMZN", timedelta(days=100))])
def test_buy_and_sell_badges_differ_in_color_and_side_of_their_own_bar(dash, page_at, symbol, ago):
    page = arm_page(page_at, "trades")
    link(page, "trades", symbol).click()
    assert_trade_framed(page, dash, ago)
    assert page.evaluate("css('--buy') !== css('--sell')")
    buy, sell = dash.now - ago, dash.now - ago + timedelta(hours=1)
    bought = badge_pixels(page, buy, "--buy")
    assert bought["below"] > 0 and bought["above"] == 0
    sold = badge_pixels(page, sell, "--sell")
    assert sold["above"] > 0 and sold["below"] == 0


def test_fill_older_than_chart_window_draws_no_badge(page_at):
    page = arm_page(page_at, "positions")
    page.select_option("#symbol", "AMZN")  # bought 100 days ago: before the first 1Hour bar, which fits on screen
    page.select_option("#tf", "1Hour")
    page.wait_for_function("candleSeries.data()[1]?.time - candleSeries.data()[0]?.time === 3600")
    first = datetime.fromtimestamp(page.evaluate("candleSeries.data()[0].time"), UTC)
    assert 0 <= bar_x(page, first) < page.locator("#candles").bounding_box()["width"]
    assert badge_pixels(page, first, "--buy")["below"] == 0


def test_fill_newer_than_last_bar_draws_no_badge(dash, page_at):
    dash.db.insert_fill(Fill(ARM, "TSLA", "buy", 1, 100, dash.now + timedelta(days=3), "entry"), "why", 2.0)
    page = arm_page(page_at, "positions")
    page.select_option("#symbol", "TSLA")
    page.select_option("#tf", "1Day")
    page.wait_for_function("candleSeries.data()[1]?.time - candleSeries.data()[0]?.time >= 82800")
    assert badge_pixels(page, dash.now, "--buy")["below"] == 0


def test_tap_on_bar_lists_its_fills(dash, page_at):
    page, sold = page_at_msft_sell(dash, page_at, has_touch=True)
    page.touchscreen.tap(*bar_point(page, sold))
    page.wait_for_function(f"{FILLINFO}.includes('sell 110.00 target')")


def test_hover_on_bar_lists_its_fills(dash, page_at):
    page, sold = page_at_msft_sell(dash, page_at)
    page.mouse.move(*bar_point(page, sold))
    page.wait_for_function(f"{FILLINFO}.includes('sell 110.00 target')")
    page.mouse.move(*bar_point(page, sold, dx=26))  # two bars on: no fill there
    page.wait_for_function(f"{FILLINFO} === ''")


def test_replayed_fill_badge_is_hollow(dash, page_at):
    live, replayed = dash.now - timedelta(days=20), dash.now - timedelta(days=10)
    dash.db.insert_fill(Fill(ARM, "TSLA", "buy", 1, 100, live, "entry"), "why", 2.0)
    dash.db.insert_fill(Fill(ARM, "TSLA", "buy", 1, 100, replayed, "entry", True), "why", 2.0)
    page = arm_page(page_at, "positions")
    page.select_option("#symbol", "TSLA")
    page.select_option("#tf", "1Day")
    page.wait_for_function(f"candleSeries.data()[0]?.time < {live.timestamp()}")
    page.evaluate(f"candleChart.timeScale().setVisibleRange({{ from: {live.timestamp() - 86400 * 5}, to: {replayed.timestamp() + 86400 * 5} }})")
    assert 0 < badge_pixels(page, replayed, "--buy")["below"] < badge_pixels(page, live, "--buy")["below"]
    page.mouse.move(*bar_point(page, replayed))
    page.wait_for_function(f"{FILLINFO}.includes('(replayed)')")


def test_clicked_trade_stays_solid_and_other_trades_fade(dash, page_at):
    earlier, later = timedelta(days=10), timedelta(days=10) - timedelta(hours=3)
    dash.closed("MSFT", later)
    page = arm_page(page_at, "trades")
    link(page, "trades", "MSFT").first.click()  # newest first: the later trade
    assert_trade_framed(page, dash, later)
    assert badge_pixels(page, dash.now - later, "--buy")["below"] > 0
    assert badge_pixels(page, dash.now - earlier, "--buy")["below"] == 0
    picked_row = page.locator("#trades tr:has(.stocklink.on)")
    assert picked_row.count() == 1
    page.evaluate(POLL_ARM)
    assert picked_row.count() == 1
    page.click('button[data-tab="positions"]')  # whole chart there: no trade picked
    page.wait_for_function("!focused")
    assert badge_pixels(page, dash.now - earlier, "--buy")["below"] > 0
    assert picked_row.count() == 0


# ---------- axis ----------
def test_intraday_axis_dates_new_days_and_times_the_rest(page_at):
    page = arm_page(page_at, "positions")
    page.select_option("#tf", "1Hour")
    page.wait_for_function("candleSeries.data()[1]?.time - candleSeries.data()[0]?.time === 3600")
    tick = "(type) => candleChart.options().timeScale.tickMarkFormatter(1700000000, LightweightCharts.TickMarkType[type])"
    assert page.evaluate(tick, "DayOfMonth") == "Nov 14"  # 2023-11-14 17:13 ET
    assert page.evaluate(tick, "Time") == "17:13"


VISIBLE = "candleChart.timeScale().getVisibleLogicalRange()"


def test_day_chart_opens_on_recent_bars_and_week_on_all(dash, page_at):
    today = et_date(dash.now)
    dash.db.upsert_daily([Bar("GOOGL", et_at(today - timedelta(days=i), "00:00"), 100, 101, 99, 100, 1000)
                          for i in range(1, 3001)])  # at the chart's minimum bar spacing, fitting all shows only part
    page = arm_page(page_at, "positions")
    page.select_option("#symbol", "GOOGL")
    page.select_option("#tf", "1Day")
    page.wait_for_function("candleSeries.data().length > 2900")
    recent = f"{VISIBLE}.from === candleSeries.data().length - 250"
    page.wait_for_function(recent, timeout=5000)
    page.click('button[data-tab="decisions"]')  # chart hidden, then back at full width: same view
    page.wait_for_function("location.hash === '#decisions'")
    page.click('button[data-tab="positions"]')
    page.wait_for_function(recent, timeout=5000)
    page.select_option("#tf", "1Week")
    page.wait_for_function(f"candleSeries.data().length < 500 && {VISIBLE}.from <= 0", timeout=5000)


# ---------- stock links ----------
@pytest.mark.parametrize("symbol,ago,tf", [
    ("NVDA", timedelta(hours=3), "1Min"),
    ("MSFT", timedelta(days=10), "1Hour"),
    ("AMZN", timedelta(days=100), "1Day"),
])
def test_closed_trade_click_frames_it_in_finest_timeframe(dash, page_at, symbol, ago, tf):
    page = arm_page(page_at, "trades")
    link(page, "trades", symbol).click()
    page.wait_for_function(f"document.getElementById('tf').value === '{tf}'")
    assert page.input_value("#symbol") == symbol
    assert page.locator("#candles").is_visible()
    assert_trade_framed(page, dash, ago)


@pytest.mark.parametrize("width", [1400, 400])
def test_long_trade_fits_chart_at_any_width(dash, page_at, width):
    ago, hold = timedelta(hours=13), timedelta(minutes=700)  # two sessions of 1Min bars at most in real life
    dash.closed("TSLA", ago, hold)
    page = arm_page(page_at, "trades")
    page.set_viewport_size({"width": width, "height": 800})
    link(page, "trades", "TSLA").click()
    page.wait_for_function("document.getElementById('tf').value === '1Min'")
    assert_trade_framed(page, dash, ago, hold)


def test_click_frames_trade_when_response_is_instant(dash, page_at):
    page = arm_page(page_at, "trades")
    page.evaluate("""async () => {
      const real = window.fetch, done = {};
      for (const tf of BUY_TFS) {
        const url = `/api/candles?symbol=MSFT&arm=${encodeURIComponent(window.ARM)}&tf=${tf}`;
        done[url] = await real(url).then((r) => r.json());
      }
      window.fetch = (url, ...rest) => done[url]
        ? Promise.resolve({ json: () => Promise.resolve(done[url]) }) : real(url, ...rest);
    }""")
    link(page, "trades", "MSFT").click()
    assert_trade_framed(page, dash, timedelta(days=10))


def test_open_position_click_keeps_timeframe_and_tab(page_at):
    page = arm_page(page_at, "positions")
    page.select_option("#tf", "1Hour")
    length = page.evaluate("history.length")
    link(page, "positions", "GOOGL").click()
    page.wait_for_function("document.getElementById('symbol').value === 'GOOGL'")
    assert page.input_value("#tf") == "1Hour"
    assert page.evaluate("history.length") == length
    assert page.url.endswith("#positions")


def test_stock_out_of_universe_stays_plain_text(page_at):
    page = arm_page(page_at, "trades")
    assert page.locator("#trades td", has_text="DELISTED").count() == 1
    assert link(page, "trades", "DELISTED").count() == 0


def test_later_click_beats_load_in_flight(dash, page_at):
    page = arm_page(page_at, "trades")
    held = []
    page.route("**/api/candles?symbol=MSFT*", lambda r: held.append(r))  # MSFT answers only after AMZN has drawn
    link(page, "trades", "MSFT").click()
    while not held:
        page.wait_for_timeout(10)
    link(page, "trades", "AMZN").click()
    assert_trade_framed(page, dash, timedelta(days=100))
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/candles" in r.url else None)
    held[0].continue_()
    page.wait_for_timeout(300)
    assert asked == []  # MSFT's load stopped: no further timeframe tried, nothing drawn over AMZN
    assert page.input_value("#tf") == "1Day"
    assert_trade_framed(page, dash, timedelta(days=100))


def test_closed_trade_click_stays_on_closed_trades(page_at):
    page = arm_page(page_at, "trades")
    length = page.evaluate("history.length")
    link(page, "trades", "MSFT").click()
    page.wait_for_function("document.getElementById('tf').value === '1Hour'")
    assert page.url.endswith("#trades")
    assert page.evaluate("history.length") == length
    assert page.locator("#trades").is_visible() and page.locator("#candles").is_visible()


def test_tab_switch_during_trade_load_shows_whole_chart(dash, page_at):
    page = arm_page(page_at, "trades")
    held = []
    page.route("**/api/candles?symbol=MSFT*tf=1Min*", lambda r: held.append(r))
    link(page, "trades", "MSFT").click()
    while not held:
        page.wait_for_timeout(10)
    page.click('button[data-tab="positions"]')
    page.wait_for_function("!focused")
    held[0].continue_()
    page.wait_for_function("document.getElementById('tf').value === '1Hour'")
    page.wait_for_timeout(300)  # the load's own framing, if any, lands by now
    shown = page.evaluate("(({ from, to }) => to - from)(candleChart.timeScale().getVisibleLogicalRange())")
    assert shown >= page.evaluate("candleSeries.data().length") - 2


def test_chart_only_on_tabs_that_link_to_it(page_at):
    page = page_at(f"/arm/{ARM}#decisions")
    assert not page.locator("#candles").is_visible()
    page.click('button[data-tab="trades"]')
    page.wait_for_function("location.hash === '#trades'")
    assert page.locator("#candles").is_visible()
