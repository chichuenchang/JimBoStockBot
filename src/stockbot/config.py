"""Load and validate config/*.yaml into frozen dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelCfg:
    name: str
    adapter: Literal["openai_compat", "anthropic", "fake"]
    base_url: str | None
    model: str
    api_key_env: str | None
    price_in: float
    price_in_cached: float
    price_out: float
    monthly_cap_usd: float
    enabled: bool
    extra_body: dict | None = None  # merged into the request body, e.g. thinking switches


@dataclass(frozen=True)
class ArmCfg:
    name: str
    model: str
    variant: Literal["baseline", "improved"]
    enabled: bool


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]
    start_nav: float
    slippage_bps: float
    commission_per_order: float
    cycle_start: str
    cycle_end: str
    cycle_every_min: int
    report_times: tuple[str, ...]
    analyst_model: str
    global_cap_usd: float
    budget_warn_frac: float
    db_path: Path
    dashboard_port: int
    risk_per_trade: float
    max_position_frac: float
    max_positions: int
    max_entries_per_day: int
    min_rr: float
    min_stop_atr: float
    daily_loss_freeze: float
    max_wakes_per_day: int
    llm_timeout_s: float
    benchmark_symbol: str = "QQQ"


@dataclass(frozen=True)
class Event:
    day: date
    time_et: str | None
    name: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    models: dict[str, ModelCfg]
    arms: tuple[ArmCfg, ...]
    events: tuple[Event, ...]


ADAPTERS = ("openai_compat", "anthropic", "fake")
VARIANTS = ("baseline", "improved")


def _read(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing config file {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _settings(raw: dict, base: Path) -> Settings:
    names = {f.name for f in fields(Settings)}
    unknown = set(raw) - names
    if unknown:
        raise ConfigError(f"unknown settings keys: {sorted(unknown)}")
    raw = dict(raw)
    try:
        raw["universe"] = tuple(raw["universe"])
        raw["report_times"] = tuple(raw["report_times"])
        db = Path(raw["db_path"])
        raw["db_path"] = db if db.is_absolute() else base / db
        return Settings(**raw)
    except (KeyError, TypeError) as e:
        raise ConfigError(f"settings.yaml: {e}") from e


def load_config(config_dir: Path) -> AppConfig:
    config_dir = Path(config_dir)
    settings = _settings(_read(config_dir / "settings.yaml"), config_dir.parent)

    models: dict[str, ModelCfg] = {}
    for name, m in (_read(config_dir / "models.yaml").get("models") or {}).items():
        try:
            cfg = ModelCfg(name=name, **m)
        except TypeError as e:
            raise ConfigError(f"model {name}: {e}") from e
        if cfg.adapter not in ADAPTERS:
            raise ConfigError(f"model {name}: adapter {cfg.adapter!r} not in {ADAPTERS}")
        models[name] = cfg

    arms = []
    for a in _read(config_dir / "arms.yaml").get("arms") or []:
        try:
            arm = ArmCfg(**a)
        except TypeError as e:
            raise ConfigError(f"arm {a.get('name', '?')}: {e}") from e
        if arm.model not in models:
            raise ConfigError(f"arm {arm.name}: unknown model {arm.model!r}")
        if arm.variant not in VARIANTS:
            raise ConfigError(f"arm {arm.name}: variant {arm.variant!r} not in {VARIANTS}")
        if arm.enabled and not models[arm.model].enabled:
            raise ConfigError(f"arm {arm.name}: model {arm.model} is disabled")
        arms.append(arm)
    if len({a.name for a in arms}) != len(arms):
        raise ConfigError("duplicate arm names")

    if settings.analyst_model not in models:
        raise ConfigError(f"analyst_model {settings.analyst_model!r} unknown")

    caps = sum(m.monthly_cap_usd for m in models.values() if m.enabled)
    if caps > settings.global_cap_usd:
        raise ConfigError(
            f"enabled model caps ${caps} exceed global cap ${settings.global_cap_usd}")

    events = tuple(
        Event(day=e["day"], time_et=e.get("time_et"), name=e["name"],
              symbols=tuple(e.get("symbols") or ()))
        for e in (_read(config_dir / "events.yaml").get("events") or []))

    return AppConfig(settings=settings, models=models, arms=tuple(arms), events=events)


def api_key(m: ModelCfg) -> str:
    """Key from environment (.env loaded on demand). Never logs the value."""
    if not m.api_key_env:
        raise ConfigError(f"model {m.name}: no api_key_env set")
    load_dotenv()
    value = os.environ.get(m.api_key_env)
    if not value:
        raise ConfigError(f"model {m.name}: environment variable {m.api_key_env} not set")
    return value
