"""気圧の道具の試験。通信は使わず、OpenWeather の返しは偽物で置き換える。"""

from __future__ import annotations

import json
import logging
import urllib.error

import pytest

from conftest import FAKE_KEY, current_payload, forecast_payload
from omonpakari import pressure, storage
from omonpakari.settings import Thresholds

H = 3600


def _routes(hermes, now, now_hpa=1012.0, forecast=None, current_status=200, forecast_status=200):
    hermes.http.routes[pressure.CURRENT_URL] = (current_status, current_payload(now - 600, now_hpa, tz_s=9 * H))
    hermes.http.routes[pressure.FORECAST_URL] = (
        forecast_status, forecast_payload(now + H, forecast if forecast is not None else [1011.0] * 8, tz_s=9 * H))


def _run(settings, **args):
    return json.loads(pressure.make_handler(settings)(args))


# --- 鍵と座標 ---------------------------------------------------------------

def test_鍵が無い時は理由の印だけを返して通信に出ない(hermes, settings):
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ok"] is False and out["reason"] == "missing_key"
    assert "OPENWEATHER_API_KEY" in out["hint"]
    assert hermes.http.calls == []


def test_座標が無い時は理由の印を返す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    out = _run(settings)
    assert out == {"ok": False, "reason": "location_not_set", "hint": pressure.REASON_HINTS["location_not_set"]}
    assert hermes.http.calls == []


@pytest.mark.parametrize("lat, lon", [(91.0, 0.0), (0.0, 181.0), ("north", 0.0), (True, 0.0)])
def test_座標の範囲の外は誤りの印(hermes, settings, lat, lon):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    assert _run(settings, lat=lat, lon=lon)["reason"] == "invalid_location"


def test_設定の座標を使う(hermes, ctx, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    ctx.config.update(latitude=0.0, longitude=0.0)
    _routes(hermes, hermes.clock.now)
    assert _run(settings)["ok"] is True
    assert hermes.http.calls[0][1]["lat"] == "0.0000"


# --- 取りと判じ ---------------------------------------------------------------

def test_今の値とこの先の変化を返す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    now = hermes.clock.now
    _routes(hermes, now, now_hpa=1008.0, forecast=[1007, 1006, 1004, 1003, 1001, 1000, 1001, 1002, 1003])
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ok"] is True and out["source"] == "live"
    assert out["now"]["hpa"] == 1008.0
    assert out["now"]["at"].endswith("+09:00")  # 場所の時差で返す
    assert out["level"] == "low"
    # 今の値は10分前の実測。24時間の窓の内の最後の予報点（今から23時間後）との差
    assert out["ahead"]["delta_hpa"] == pytest.approx(-6.0)
    assert out["ahead"]["over_h"] == pytest.approx(22.2, abs=0.1)
    assert out["ahead"]["lowest_hpa"] == 1000.0
    assert out["trend"] == "falling" and out["falling_fast"] is True
    assert out["recent"] is None  # 溜めがまだ無い
    assert out["thresholds"]["low_hpa"] == 1010.0


def test_予報が足りない時は見えた幅だけを名乗る(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    now = hermes.clock.now
    _routes(hermes, now, now_hpa=1012.0, forecast=[1011.0, 1010.0])
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ahead"]["over_h"] < 6
    assert out["ahead"]["delta_hpa"] == pytest.approx(-2.0)
    assert out["trend"] == "steady"


def test_予報の取りは40点で返す点は時間で切る(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    now = hermes.clock.now
    _routes(hermes, now, forecast=[1010.0] * 40)
    out = _run(settings, lat=0.0, lon=0.0, hours_ahead=6)
    forecast_call = [c for c in hermes.http.calls if c[0] == pressure.FORECAST_URL][0]
    assert forecast_call[1]["cnt"] == 40
    assert len(out["forecast"]) == 2  # 1時間後と4時間後（7時間後は6時間の外）


def test_hours_aheadは範囲に収める(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now, forecast=[1010.0] * 40)
    assert len(_run(settings, lat=0.0, lon=0.0, hours_ahead=0)["forecast"]) == 1
    hermes.clock.now += 3600  # 取り直しの後も、返す点の数は上限の120時間で切る
    assert len(_run(settings, lat=0.0, lon=0.0, hours_ahead=999)["forecast"]) >= 39


def test_30分以内は溜めを使い回す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    now = hermes.clock.now
    _routes(hermes, now)
    assert _run(settings, lat=0.0, lon=0.0)["source"] == "live"
    calls = len(hermes.http.calls)
    hermes.clock.now += 20 * 60
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["source"] == "cache" and out["ok"] is True
    assert len(hermes.http.calls) == calls
    assert len(out["forecast"]) > 0


def test_古い溜めは取り直す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now)
    _run(settings, lat=0.0, lon=0.0)
    hermes.clock.now += 31 * 60
    _routes(hermes, hermes.clock.now)
    assert _run(settings, lat=0.0, lon=0.0)["source"] == "live"


def test_過去の変化は溜めた線から求める(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    start = hermes.clock.now
    for i, hpa in enumerate([1016.0, 1014.0, 1012.0, 1010.0, 1008.0]):
        hermes.clock.now = start + i * 6 * H
        _routes(hermes, hermes.clock.now, now_hpa=hpa, forecast=[])
        out = _run(settings, lat=0.0, lon=0.0)
    assert out["recent"]["delta_hpa"] == pytest.approx(-8.0)
    assert out["recent"]["over_h"] == 24.0
    assert out["falling_fast"] is True
    assert out["ahead"] is None  # 予報が無い時は、過去の変化で trend を判じる
    assert out["trend"] == "falling"


@pytest.mark.parametrize("hpa, level", [
    (1002.9, "very_low"), (1003.0, "low"), (1009.9, "low"), (1010.0, "normal"),
    (1020.0, "normal"), (1020.1, "high"),
])
def test_判じの境目(hpa, level):
    assert pressure.level_of(hpa, Thresholds()) == level


@pytest.mark.parametrize("delta, trend", [(None, "unknown"), (-6.0, "falling"), (-5.9, "steady"),
                                          (5.9, "steady"), (6.0, "rising")])
def test_変化の向きの境目(delta, trend):
    assert pressure.trend_of(delta, Thresholds()) == trend


def test_境目は設定で変えられる(hermes, ctx, settings):
    ctx.config.update(low_hpa=1000, very_low_hpa=990, high_hpa=1030, drop_hpa=-3, window_h=500)
    th = settings.thresholds()
    assert (th.low_hpa, th.very_low_hpa, th.high_hpa, th.drop_hpa, th.window_h) == (1000, 990, 1030, 3, 72)
    assert pressure.level_of(1005, th) == "normal"


# --- 失敗の扱い ---------------------------------------------------------------

@pytest.mark.parametrize("status, reason", [(401, "auth_failed"), (429, "rate_limited"),
                                            (500, "http_500"), (404, "http_404")])
def test_応答の番号ごとの印(hermes, settings, status, reason):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now, current_status=status)
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ok"] is False and out["reason"] == reason


def test_通信の失敗の戻りと帳に鍵と座標が出ない(hermes, settings, caplog):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    leaky = (f"HTTPSConnectionPool: Max retries exceeded with url: /data/2.5/weather?lat=12.3456&lon=65.4321"
             f"&appid={FAKE_KEY}&units=metric")
    hermes.http.routes[pressure.CURRENT_URL] = urllib.error.URLError(leaky)
    with caplog.at_level(logging.DEBUG, logger="omonpakari"):
        raw = pressure.make_handler(settings)({"lat": 12.3456, "lon": 65.4321})
    assert json.loads(raw)["reason"] == "network"
    for secret in (FAKE_KEY, "12.3456", "65.4321"):
        assert secret not in raw
        assert secret not in caplog.text


def test_JSONの崩れは印にする(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    hermes.http.routes[pressure.CURRENT_URL] = (200, b"<html>not json")
    assert _run(settings, lat=0.0, lon=0.0)["reason"] == "bad_response"


@pytest.mark.parametrize("body", [[1, 2, 3], {"main": {}}, {"dt": 1, "main": {"pressure": 5}}])
def test_読めない応答は印にする(hermes, settings, body):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    hermes.http.routes[pressure.CURRENT_URL] = (200, body)
    assert _run(settings, lat=0.0, lon=0.0)["reason"] == "bad_response"


def test_予報だけ落ちても今の値は返す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now, forecast_status=503)
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ok"] is True and out["ahead"] is None and out["forecast"] == []


def test_記録の置き場が開けなくても取れた値は返す(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    hermes.db.broken = True
    _routes(hermes, hermes.clock.now)
    out = _run(settings, lat=0.0, lon=0.0)
    assert out["ok"] is True and out["now"]["hpa"] == 1012.0


def test_思わぬ誤りでも例外を投げずに返す(hermes, settings, monkeypatch):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    monkeypatch.setattr(pressure, "refresh", lambda *a, **k: 1 / 0)
    assert _run(settings, lat=0.0, lon=0.0) == {"ok": False, "reason": "internal_error"}


def test_鍵の範囲が据わらない器でも黙って鍵無しとして返す(hermes, settings, monkeypatch):
    import sys

    def unscoped(name, default=None):
        raise RuntimeError("no profile secret scope active")

    monkeypatch.setattr(sys.modules["agent.secret_scope"], "get_secret", unscoped)
    assert _run(settings, lat=0.0, lon=0.0)["reason"] == "missing_key"


# --- 伏せと座標の覚え ---------------------------------------------------------

def test_問いの住所の値を伏せる():
    text = "GET /weather?lat=1.5&lon=2.5&appid=abc123 failed for abc123"
    out = pressure.redact(text, ("abc123",))
    assert "abc123" not in out and "1.5" not in out and "2.5" not in out
    assert "appid=***" in out


def test_rememberの時だけ座標を丸めて残す(hermes, ctx, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now)
    out = _run(settings, lat=1.23456, lon=2.34567)
    assert "location_saved" not in out and "latitude" not in ctx.config
    hermes.clock.now += 3600
    _routes(hermes, hermes.clock.now)
    out = _run(settings, lat=1.23456, lon=2.34567, remember=True)
    assert out["location_saved"] is True
    assert ctx.config == {"latitude": 1.23, "longitude": 2.35}


def test_溜めには座標を置かない(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    _routes(hermes, hermes.clock.now)
    _run(settings, lat=12.3456, lon=65.4321)
    import sqlite3
    dump = "\n".join(sqlite3.connect(hermes.db.path).iterdump())
    assert "12.34" not in dump and "65.43" not in dump
    assert storage.place_key(12.3456, 65.4321) in dump


def test_別の場所の溜めと差を取らない(hermes, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    start = hermes.clock.now
    for i in range(5):  # いつもの場所で、24時間ずっと 1015
        hermes.clock.now = start + i * 6 * H
        _routes(hermes, hermes.clock.now, now_hpa=1015.0, forecast=[])
        _run(settings, lat=0.0, lon=0.0)
    hermes.clock.now += H
    _routes(hermes, hermes.clock.now, now_hpa=1000.0, forecast=[])
    out = _run(settings, lat=10.0, lon=10.0)  # 旅先を一度だけ聞く
    assert out["recent"] is None and out["falling_fast"] is False
