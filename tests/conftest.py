"""試験の支度。

- Hermes が手元に無くても走るように、本家の agent.secret_scope と plugins.plugin_storage を偽物で置く。
- 通信には出ない。OpenWeather の返しは偽物（FakeHttp）で置き換える。
- 偽の値は、どれも誰のものでもない形にする（test-key、緯度経度 0.0 など）。
- プラグインの家は、本家の読み込みと同じ形（家を submodule の道にした spec）で "omonpakari" として読む。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE_KEY = "test-key-not-real"


def _load_plugin():
    if "omonpakari" in sys.modules:
        return sys.modules["omonpakari"]
    spec = importlib.util.spec_from_file_location(
        "omonpakari", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["omonpakari"] = module
    spec.loader.exec_module(module)
    return module


_load_plugin()

from omonpakari import quiet_context, storage  # noqa: E402


class FakeDb:
    """本家の plugin_db の代わり。毎回新しい接続を、同じ紙で開く。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.opened = 0
        self.broken = False

    def plugin_db(self, name: str, filename: str = "data.db") -> sqlite3.Connection:
        assert name == "omonpakari"
        if self.broken:
            raise sqlite3.OperationalError("disk I/O error")
        self.opened += 1
        return sqlite3.connect(self.path, check_same_thread=False)


class FakeHttp:
    """OpenWeather の偽物。住所ごとに (状態, 本文) か例外を返す。呼ばれた問いを録る。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, float]] = []
        self.routes: dict[str, object] = {}

    def __call__(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params), timeout))
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f"unexpected request to {url}")
        if isinstance(route, BaseException):
            raise route
        status, body = route
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        return status, body


class FakeCtx:
    """register(ctx) を録る ctx の代わり。設定は辞書で持つ。"""

    def __init__(self, config: dict | None = None) -> None:
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, list] = {}
        self.skills: dict[str, dict] = {}
        self.config = dict(config or {})

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler,
                            "check_fn": check_fn, "requires_env": requires_env, "override": override}

    def register_hook(self, hook_name, callback, *, hold_stream=False):
        self.hooks.setdefault(hook_name, []).append(callback)

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills[name] = {"path": Path(path), "description": description}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def current_payload(dt: int, hpa: float, tz_s: int = 0) -> dict:
    return {"dt": dt, "main": {"pressure": hpa, "temp": 12.5, "humidity": 70}, "timezone": tz_s}


def forecast_payload(start_dt: int, hpas: list[float], tz_s: int = 0, step_s: int = 3 * 3600) -> dict:
    return {"list": [{"dt": start_dt + i * step_s, "main": {"pressure": p, "temp": 12.0, "humidity": 70}}
                     for i, p in enumerate(hpas)],
            "city": {"timezone": tz_s}}


@pytest.fixture(autouse=True)
def hermes(tmp_path, monkeypatch):
    """偽の Hermes の口を据え、時計を止め、フックの覚えを空にする。"""
    secrets: dict[str, str] = {}

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []
    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = lambda name, default=None: secrets.get(name, default)
    plugins_pkg = types.ModuleType("plugins")
    plugins_pkg.__path__ = []
    plugin_storage = types.ModuleType("plugins.plugin_storage")
    db = FakeDb(tmp_path / "data.db")
    plugin_storage.plugin_db = db.plugin_db
    # 公の口の偽物：config.yaml の上の段（timezone ほか）と、回の出どころ（HERMES_SESSION_SOURCE）。
    # 既定は帯が据わっていない（機械の地の時刻）、出どころも空（platform だけで見る）。
    zone = SimpleNamespace(name="", session_source="")
    cli_pkg = types.ModuleType("hermes_cli")
    cli_pkg.__path__ = []
    cli_config = types.ModuleType("hermes_cli.config")
    cli_config.load_config_readonly = lambda: {"timezone": zone.name} if zone.name else {}
    gateway_pkg = types.ModuleType("gateway")
    gateway_pkg.__path__ = []
    session_context = types.ModuleType("gateway.session_context")
    session_context.get_session_env = (
        lambda name, default="": zone.session_source if name == "HERMES_SESSION_SOURCE" else default)
    for name, module in (("agent", agent_pkg), ("agent.secret_scope", scope),
                         ("hermes_cli", cli_pkg), ("hermes_cli.config", cli_config),
                         ("gateway", gateway_pkg), ("gateway.session_context", session_context),
                         ("plugins", plugins_pkg), ("plugins.plugin_storage", plugin_storage)):
        monkeypatch.setitem(sys.modules, name, module)

    http = FakeHttp()
    monkeypatch.setattr("omonpakari.pressure.HTTP_GET", http)

    clock = SimpleNamespace(now=1_800_000_000)  # 2027-01-15 08:00 UTC
    monkeypatch.setattr(storage, "now_utc", lambda: clock.now)

    spawned: list = []
    monkeypatch.setattr(quiet_context, "SPAWN", lambda work: spawned.append(work))
    monkeypatch.setattr(quiet_context, "_refresh_lock", threading.Lock())  # 走らせずに残った仕事の錠を持ち越さない
    quiet_context._placed.clear()
    quiet_context._last_refresh.clear()

    yield SimpleNamespace(secrets=secrets, db=db, http=http, clock=clock, spawned=spawned, zone=zone)


@pytest.fixture
def ctx():
    return FakeCtx()


@pytest.fixture
def settings(ctx):
    from omonpakari.settings import Settings
    return Settings(ctx)
