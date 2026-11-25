# JBSB Miaoda mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (user CLAUDE.md: no subagents). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Daemon pushes rendered JBSB pages + API bodies to a login-walled Miaoda socket Jim opens from phone/PC.

**Architecture:** Dashboard gains `root` prefix + `mirror` flag. `stockbot.mirror` crawls a second in-process app via `httpx.ASGITransport`, pushes generations to `../jbsb-miaoda` (NestJS, raw SQL store), which serves by key.

**Tech Stack:** Python 3.12, FastAPI, httpx, respx, pytest; Miaoda NestJS template, Postgres via platform Drizzle; lark-cli profile `brecruit`.

**Spec:** `docs/superpowers/specs/2026-11-24-jbsb-miaoda-mirror-design.md`

## Global Constraints

- lark-cli: `--profile brecruit ... --as user`, tenant `ycnx0knkk9ic`.
- Access scope `specific` = Jim only. API key / push secret never in git, logs, commit messages, chat.
- No socket route changes trading or local DB. Write routes absent from mirror.
- Push errors logged, never raised into daemon step.
- `.env`: `JBSB_MIAODA_URL`, `JBSB_MIAODA_KEY`; missing = mirror off.
- Commits: one line, 5 words, no trailer (user). Miaoda repo commits too.
- Line endings: Edit tool / `newline=""`; no `sed -i` on tree (memory).

## Review Focus

- Socket mounted under `/app/<id>`: any link/fetch missing `ROOT` breaks on phone only — test scans rendered HTML + JS (Task 2).
- PC asleep: page must say how old it is — banner from push time (Task 2, Task 5).
- Crawl hitting Alpaca 1 call per candle key per arm — `MemoData` dedupes; test counts calls (Task 3).
- Push dies mid-generation: old gen keeps serving — socket commit test by hand + client test that commit only after all batches ok (Task 4).
- Daemon start without `.env` keys: no task, one log line, no crash (Task 4).

---

### Task 1: Spike socket, prove push path

**Files:** Create `../jbsb-miaoda` (Miaoda repo). Modify spec §Risks with measured answers.

- [ ] `lark-cli --profile brecruit apps +create` full_stack named `JBSB`; record app_id. `+feature-enable --feature database`. `+init` into `../jbsb-miaoda`; GCM helper lines (BRecruitHelper `CLAUDE.md` §Miaoda repos).
- [ ] Copy creatorio `app.module.ts` shape: `PlatformModule.forRoot({ enableCsrf: false })`, one module `server/modules/jbsb/` with `GET /` → text `jbsb spike`, `POST /openapi/jbsb/ping` → echo body length + headers seen.
- [ ] Commit (`Spike socket ping route only`), push `sprint/default`, `+release-create`, poll `+release-get` finished.
- [ ] Jim's open_id: `lark-cli --profile brecruit contact +get-user --as user` (or `auth status`). `+access-scope-set --scope specific --targets '[{"type":"user","id":"<ou_>"}]'`.
- [ ] `+openapi-key-create --name jbsb-push --scope-api 'POST /openapi/jbsb/ping'`; write key to StockBot `.env` `JBSB_MIAODA_KEY` straight from JSON (pipe, no echo).
- [ ] Probe with Python httpx: unauthenticated `GET /` (expect login wall), `POST /openapi/jbsb/ping` with key as `Authorization: Bearer`, then `x-api-key` etc. until 200. Then body sizes 64 kB, 512 kB, 2 MB, 8 MB.
- [ ] Pass = some header gets 200 through login wall. Fail = fallback (spec §Risks 1): stop, tell Jim before continuing.
- [ ] Write header name + max body into spec §Risks as measured. Commit StockBot (`Record Miaoda push spike results`).

### Task 2: Root prefix and mirror flag

**Files:** Modify `src/stockbot/dashboard/app.py`, `templates/base.html`, `index.html`, `arm.html`, `static/{core,arm,candles,index,main,status,usage}.js`. Test `tests/test_dashboard.py`.

**Interfaces:**
- Produces: `create_app(db, cfg, budget, data=None, *, root: str = "", mirror: datetime | None = None) -> FastAPI`. `mirror` = crawl time; None = localhost. Template ctx `root`, `mirror`. `<body data-root="{{ root }}"{% if mirror %} data-mirror="1"{% endif %}>`. JS global `ROOT` (core.js, from `document.body.dataset.root`); `url(path)` = `ROOT + path`.
- Banner: `#mirrorstamp` in nav, server-rendered "mirror · as of HH:MM ET" from `mirror`. Crawl renders once per gen, so no runtime header needed.

- [ ] Failing tests:
  - `test_root_prefixes_every_link`: app `root="/app/x"`, `mirror=T0`; for `/`, `/usage`, `/arm/kimi-improved` every `href="/`, `src="/` in HTML starts `/app/x/`.
  - `test_localhost_links_unchanged`: root `""` → `href="/arm/kimi-improved"` present, `data-root=""`.
  - `test_js_has_no_bare_paths`: regex over `static/*.js` finds no `` ["'`]/(api|arm|static|usage) `` and no `fetch(location.pathname)` outside `url(`.
  - `test_mirror_hides_controls`: mirror page lacks `id="pausebtn"`, interval input, `draggable="true"`; has `id="mirrorstamp"` with ET time of `T0`.
- [ ] Run `uv run pytest tests/test_dashboard.py -q` — new tests fail.
- [ ] Implement. `core.js:119` `location.pathname` already includes root on socket: keep as is, whitelist in test. Status/interval/pause_all controls in status.js: skip wiring when `document.body.dataset.mirror`. Hidden via template `{% if not mirror %}`.
- [ ] Full suite `uv run pytest -q` passes; browser test `tests/test_dashboard_browser.py` too.
- [ ] Commit (`Dashboard root prefix, mirror flag`).

### Task 3: Crawl

**Files:** Create `src/stockbot/mirror.py`. Modify `src/stockbot/config.py` (`Settings.mirror_decisions: int = 50`), `config/settings.yaml` (line + comment). Test `tests/test_mirror.py`.

**Interfaces:**
- Consumes: Task 2 `create_app(..., root=, mirror=)`.
- Produces: `@dataclass(frozen=True) class Entry: content_type: str; body: bytes`. `async def crawl(db, cfg, budget, data: MarketData | None, root: str, now: datetime) -> dict[str, Entry]`. `def key(path: str, query: str) -> str` (query params sorted, `?` only if any). `class MemoData(MarketData)`: wraps one, memoizes `bars`/`calendar`/`latest_prices` by args for one crawl.

- [ ] Failing tests (fixture db from `test_dashboard.py` shape, `data=None`):
  - `test_crawl_keys`: contains `/`, `/usage`, `/arm/kimi-improved`, `/static/core.js?v=…`, `/api/status`, `/api/equity?mode=pct`, `/api/equity?arms=benchmark-…%2Ckimi-improved&mode=usd` (exact as arm.js sends, sorted), `/api/market?sessions=21`, `/api/usage?span=7d`, `/api/arm/kimi-improved/decisions?acted=1`, `/api/candles?symbol=MSFT&tf=1Day`, `/api/candles?arm=kimi-improved&symbol=MSFT&tf=1Min` (arm traded MSFT).
  - `test_crawl_no_writes`: no key under pause/resume/interval/tab_order.
  - `test_decisions_bounded`: 60 decisions inserted, `mirror_decisions=50` → 50 `/api/decision/<id>` keys, newest.
  - `test_memo_data_dedupes`: fake MarketData counting `bars` calls; crawl → calls ≤ universe × intraday tfs.
  - `test_key_sorts_query`: `key("/api/x", "b=2&a=1") == "/api/x?a=1&b=2"`.
- [ ] Run — fail.
- [ ] Implement. Arm candles only for symbols in `q.chart_fills`-having trades. Non-2xx responses skipped, logged once.
- [ ] Pass; full suite passes.
- [ ] Commit (`Crawl dashboard into mirror entries`).

### Task 4: Push client and daemon task

**Files:** Modify `src/stockbot/mirror.py`, `src/stockbot/daemon.py`, `.env.example`. Test `tests/test_mirror.py`, `tests/test_daemon.py`.

**Interfaces:**
- Produces: `async def push(entries: dict[str, Entry], url: str, key: str, client: httpx.AsyncClient, max_batch: int) -> str` (returns gen). Wire: `POST {url}/openapi/jbsb/batch` JSON `{gen, entries:[{key, content_type, body_b64}]}` per chunk ≤ `max_batch` bytes; then `POST {url}/openapi/jbsb/commit` `{gen, count, pushed_at}`. Header from Task 1. `class Mirror` with `async def tick(now) -> None`; `Mirror.from_env(ctx) -> Mirror | None`.
- Cadence: due if never pushed, or `cycle_every_min` (live interval, `stockbot.interval`) elapsed and `kv market_open == "1"`, or 60 min elapsed. Skip push if sha256 over sorted (key, body) of `/api/*` entries unchanged since last ok push (HTML carries the stamp, always differs).

- [ ] Failing tests (respx):
  - `test_push_chunks_then_commits`: 3 entries, small max_batch → ≥2 batch calls, then 1 commit with `count == 3`, same gen everywhere, key header present.
  - `test_push_no_commit_on_batch_error`: batch 500 → raises, no commit call.
  - `test_mirror_off_without_env`: `from_env` → None, logs `mirror off`.
  - `test_tick_swallows_errors`: push raises → tick returns, logs, retries next due.
  - `test_tick_cadence`: market open → pushes at +5 min not +4; closed → +60.
- [ ] Run — fail.
- [ ] Implement; daemon `run_forever` creates task looping `mirror.tick(clock.now())` every 30 s when `serve_dashboard` and `from_env` non-None. Crawl wrapped `asyncio.to_thread`? Crawl is async (ASGI) — measure in Task 5; leave inline unless >1 s loop block.
- [ ] Pass; full suite.
- [ ] Commit (`Push mirror generations from daemon`).

### Task 5: Real socket and deploy script

**Files:** `../jbsb-miaoda/server/modules/jbsb/{jbsb.controller,jbsb.store,jbsb.module}.ts`, `server/app.module.ts`. Create StockBot `scripts/deploy_miaoda.py`.

- [ ] DDL via `+db-execute` on dev branch (spec §Socket tables). Platform template audit columns per `lark-apps-db-execute.md`.
- [ ] Store: `putBatch(gen, entries)`, `commit(gen, count, pushedAt)` (checks count rows exist for gen, else 409; swaps `jbsb_current`; deletes other gens), `get(key)`.
- [ ] Controller: `GET ['/', '*']` strip `CLIENT_BASE_PATH`, build key (sorted query), serve or 404 JSON. `POST openapi/jbsb/batch|commit`. Body limit raised in `main.ts` if Task 1 showed express default.
- [ ] `npm run type:check` in repo passes.
- [ ] `scripts/deploy_miaoda.py`: port `miaoda-deploy.mjs` (no build). `--apply-reason` constant `"JBSB mirror update"`. Run it.
- [ ] Re-scope API key to batch+commit (`+openapi-key-update`). Set `.env` `JBSB_MIAODA_URL`.
- [ ] `uv run python -c` one-shot crawl+push against prod socket; record size, crawl time, push time.
- [ ] Open URL in Chrome (logged in): overview, one arm page, usage render with charts; banner time right; decision opens. Screenshot.
- [ ] Commit socket repo (`Serve mirror generations by key`); StockBot (`Add Miaoda deploy script`).

### Task 6: Live in daemon, docs

- [ ] Restart StockBot scheduled task (Python change; memory dashboard-dev-loop). Log shows push ok within 5 min (or 60 off-hours).
- [ ] README: one line under Run live: mirror URL source + `.env` keys + `scripts/deploy_miaoda.py`. Spec: Measured section (sizes, times, header, limit).
- [ ] Commit (`Document Miaoda mirror setup`).
