"""気圧の取り寄せ（OpenWeather の無料の 2.5 の口）、伏せ、判じ。

- 鍵が無い時、座標が無い時、通信が落ちた時も、例外を投げずに理由の印を返す。
- 鍵は問いの住所の中（appid）に入るので、例外の文は外へ出さない。帳に残す時も伏せる。
- 判じ（assess）は通信にも記録にも触れない純な関数にしておく。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import storage
from .settings import Settings, Thresholds, read_secret, valid_coordinates

logger = logging.getLogger(__name__)

CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
FORECAST_POINTS = 40          # 3時間刻みで5日ぶん。1回の取りで溜めを最大にする
CACHE_MAX_AGE_S = 30 * 60     # 30分以内の実測は取り直さない
MAX_GAP_H = 6.0               # 線の点の間がこれより開く所は、つながない
MIN_RECENT_SPAN_H = 3.0       # 過去の変化は、少なくともこの幅が見えた時だけ言う
MAX_BODY_BYTES = 2_000_000

REASON_HINTS = {
    "missing_key": ("OpenWeather API key is not set. The user can add OPENWEATHER_API_KEY "
                    "(free key: https://openweathermap.org/api) through the Hermes plugin settings or .env."),
    "location_not_set": ("No location is set. If the user asked about pressure, they can share a place's "
                         "latitude/longitude, or set latitude/longitude in the plugin settings."),
    "invalid_location": "Latitude must be -90..90 and longitude -180..180.",
    "auth_failed": "OpenWeather rejected the API key.",
    "rate_limited": "OpenWeather rate limit reached; try again later.",
    "network": "Could not reach OpenWeather.",
    "bad_response": "OpenWeather returned data that could not be read.",
}


class FetchError(Exception):
    """取り寄せの失敗。外へは reason の印だけを出す。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def failure(reason: str) -> dict:
    hint = REASON_HINTS.get(reason) or ("OpenWeather answered with an error." if reason.startswith("http_") else "")
    out: dict[str, Any] = {"ok": False, "reason": reason}
    if hint:
        out["hint"] = hint
    return out


# ---------------------------------------------------------------------------
# 伏せ
# ---------------------------------------------------------------------------

_QUERY_VALUE = re.compile(r"(?i)\b(appid|lat|lon)=[^&\s'\"]*")


def redact(text: str, secrets: tuple = ()) -> str:
    """問いの住所の appid、lat、lon の値と、渡された秘密の文字を伏せる。"""
    out = _QUERY_VALUE.sub(lambda m: f"{m.group(1)}=***", str(text))
    for secret in secrets:
        if secret:
            out = out.replace(str(secret), "***")
    return out


# ---------------------------------------------------------------------------
# 通信（試験では HTTP_GET を差し替える）
# ---------------------------------------------------------------------------

def _http_get(url: str, params: dict, timeout: float) -> tuple[int, bytes]:
    """GET して (状態の番号, 本文) を返す。通信の失敗だけを例外として上げる。"""
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "omonpakari-hermes-plugin", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""


HTTP_GET: Callable[[str, dict, float], tuple[int, bytes]] = _http_get


def _call(url: str, params: dict, timeout: float, secrets: tuple) -> dict:
    try:
        status, body = HTTP_GET(url, params, timeout)
    except Exception as exc:  # 通信の失敗。文には住所が入りうるので伏せて帳にだけ残す
        logger.debug("omonpakari: network error %s: %s", type(exc).__name__, redact(str(exc), secrets))
        raise FetchError("network") from None
    if status == 401:
        raise FetchError("auth_failed")
    if status == 429:
        raise FetchError("rate_limited")
    if status >= 400 or status < 200:
        raise FetchError(f"http_{status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FetchError("bad_response") from None
    if not isinstance(payload, dict):
        raise FetchError("bad_response")
    return payload


def parse_current(payload: dict) -> dict:
    """/weather の応答から実測の点を作る。"""
    try:
        main = payload["main"]
        row = {
            "kind": "observed",
            "at_utc": int(payload["dt"]),
            "hpa": float(main["pressure"]),
            "temp_c": _maybe_float(main.get("temp")),
            "humidity": _maybe_float(main.get("humidity")),
            "tz_min": int(payload.get("timezone") or 0) // 60,
        }
    except (KeyError, TypeError, ValueError):
        raise FetchError("bad_response") from None
    if not 800.0 <= row["hpa"] <= 1100.0:
        raise FetchError("bad_response")
    return row


def parse_forecast(payload: dict) -> list[dict]:
    """/forecast の応答から予報の点を作る。読めない点は飛ばす。"""
    items = payload.get("list")
    if not isinstance(items, list):
        raise FetchError("bad_response")
    tz_min = 0
    city = payload.get("city")
    if isinstance(city, dict):
        try:
            tz_min = int(city.get("timezone") or 0) // 60
        except (TypeError, ValueError):
            tz_min = 0
    rows = []
    for item in items:
        try:
            main = item["main"]
            hpa = float(main["pressure"])
            if not 800.0 <= hpa <= 1100.0:
                continue
            rows.append({
                "kind": "forecast",
                "at_utc": int(item["dt"]),
                "hpa": hpa,
                "temp_c": _maybe_float(main.get("temp")),
                "humidity": _maybe_float(main.get("humidity")),
                "tz_min": tz_min,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def fetch(lat: float, lon: float, key: str, timeout: float = 5.0) -> tuple[dict, list[dict]]:
    """今の実測と予報を取る。予報だけ落ちた時は、実測だけを返す。"""
    params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "appid": key, "units": "metric"}
    secrets = (key, params["lat"], params["lon"])
    observed = parse_current(_call(CURRENT_URL, params, timeout, secrets))
    try:
        forecast = parse_forecast(_call(FORECAST_URL, {**params, "cnt": FORECAST_POINTS}, timeout, secrets))
    except FetchError as exc:
        logger.debug("omonpakari: forecast unavailable (%s)", exc.reason)
        forecast = []
    return observed, forecast


# ---------------------------------------------------------------------------
# 判じ（純な関数）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Assessment:
    level: str
    trend: str
    falling_fast: bool
    ahead_delta: Optional[float]
    ahead_span_h: Optional[float]
    lowest_ahead: Optional[tuple[int, float]]
    recent_delta: Optional[float]
    recent_span_h: Optional[float]


def level_of(hpa: float, th: Thresholds) -> str:
    if hpa < th.very_low_hpa:
        return "very_low"
    if hpa < th.low_hpa:
        return "low"
    if hpa > th.high_hpa:
        return "high"
    return "normal"


def trend_of(delta: Optional[float], th: Thresholds) -> str:
    if delta is None:
        return "unknown"
    if delta <= -th.drop_hpa:
        return "falling"
    if delta >= th.drop_hpa:
        return "rising"
    return "steady"


def assess(now_at: int, now_hpa: float, forecast: list[tuple[int, float]],
           past: list[tuple[int, float, str]], th: Thresholds) -> Assessment:
    """今の値、この先の予報、溜めた過去の線から、気圧の様子を判じる。

    差には、実際に見えた時間の幅を添える（予報や溜めが足りない時に、決めた幅を名乗らない）。
    """
    window_s = th.window_h * 3600

    ahead = [(at, hpa) for at, hpa in forecast if now_at < at <= now_at + window_s]
    ahead_delta = ahead_span = None
    lowest = None
    if ahead:
        end_at, end_hpa = ahead[-1]
        ahead_delta = round(end_hpa - now_hpa, 1)
        ahead_span = round((end_at - now_at) / 3600, 1)
        lowest = min(ahead, key=lambda p: (p[1], p[0]))

    series = sorted([p for p in past if p[0] < now_at] + [(now_at, now_hpa, "observed")])
    recent_delta = recent_span = None
    start = storage.interpolate(series, now_at - window_s, MAX_GAP_H)
    if start is not None:
        recent_delta, recent_span = round(now_hpa - start, 1), float(th.window_h)
    else:
        inside = [p for p in series if now_at - window_s <= p[0] < now_at]
        if inside and (now_at - inside[0][0]) / 3600 >= MIN_RECENT_SPAN_H:
            recent_delta = round(now_hpa - inside[0][1], 1)
            recent_span = round((now_at - inside[0][0]) / 3600, 1)

    trend = trend_of(ahead_delta if ahead_delta is not None else recent_delta, th)
    falling_fast = any(d is not None and d <= -th.drop_hpa for d in (ahead_delta, recent_delta))
    return Assessment(level_of(now_hpa, th), trend, falling_fast, ahead_delta, ahead_span,
                      lowest, recent_delta, recent_span)


# ---------------------------------------------------------------------------
# 取りと溜めをまとめた運び
# ---------------------------------------------------------------------------

def refresh(lat: float, lon: float, key: str, timeout: float = 5.0,
            max_age_s: int = CACHE_MAX_AGE_S) -> dict:
    """溜めが新しければ使い回し、古ければ取りに行って溜める。

    戻りは {"source", "observed", "forecast", "place"}。取りの失敗は FetchError。
    記録の置き場が開けない時も、取れた値はそのまま返す。
    """
    place = storage.place_key(lat, lon)
    now = storage.now_utc()
    try:
        with storage.connect() as conn:
            cached = storage.latest_observed(conn, place)
            if cached and now - cached["fetched_at_utc"] <= max_age_s:
                cached["kind"] = "observed"
                forecast = storage.latest_forecast(conn, place, cached["at_utc"])
                return {"source": "cache", "observed": cached, "forecast": forecast, "place": place}
    except Exception as exc:
        logger.debug("omonpakari: storage unavailable for cache (%s)", type(exc).__name__)

    observed, forecast_rows = fetch(lat, lon, key, timeout)
    observed["fetched_at_utc"] = now
    try:
        with storage.connect() as conn:
            storage.save_samples(conn, [observed] + forecast_rows, now, place)
    except Exception as exc:
        logger.debug("omonpakari: storage unavailable for save (%s)", type(exc).__name__)
    forecast = [(r["at_utc"], r["hpa"]) for r in forecast_rows if r["at_utc"] > observed["at_utc"]]
    return {"source": "live", "observed": observed, "forecast": forecast, "place": place}


def past_series(now_at: int, th: Thresholds, place: str) -> list[tuple[int, float, str]]:
    """同じ場所の溜めた線。別の場所の値と差を取って、偽の急な変化を作らない。"""
    try:
        with storage.connect() as conn:
            return storage.load_series(conn, now_at - (th.window_h + int(MAX_GAP_H)) * 3600, now_at,
                                       place=place)
    except Exception as exc:
        logger.debug("omonpakari: storage unavailable for series (%s)", type(exc).__name__)
        return []


# ---------------------------------------------------------------------------
# 道具 omonpakari_pressure
# ---------------------------------------------------------------------------

TOOL_NAME = "omonpakari_pressure"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Barometric pressure where the user is: the current value, how it changed over the past hours "
        "and how it is forecast to change (OpenWeather). Use it when the user asks about pressure or "
        "weather, or talks about how they feel or about plans where pressure may matter. Returns facts "
        "only. Do not use it to start a conversation about health."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hours_ahead": {"type": "integer", "minimum": 3, "maximum": 120, "default": 24,
                            "description": "How many hours of forecast points to return (3-120)."},
            "lat": {"type": "number", "description": "Latitude in decimal degrees. Pass only coordinates "
                    "the user gave; otherwise omit to use the saved location."},
            "lon": {"type": "number", "description": "Longitude in decimal degrees. Pass only coordinates "
                    "the user gave; otherwise omit to use the saved location."},
            "remember": {"type": "boolean", "description": "Save lat/lon (rounded to about 1 km) as the "
                         "user's location. Only when the user asks for it."},
        },
        "additionalProperties": False,
    },
}


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def run(settings: Settings, args: dict) -> dict:
    """道具の中身。戻りは JSON にできる辞書で、例外は投げない。"""
    args = args if isinstance(args, dict) else {}
    th = settings.thresholds()
    hours_ahead = _clamp_int(args.get("hours_ahead"), 24, 3, 120)

    key = read_secret()
    if not key:
        return failure("missing_key")

    given = args.get("lat") is not None or args.get("lon") is not None
    if given:
        coords = valid_coordinates(args.get("lat"), args.get("lon"))
        if coords is None:
            return failure("invalid_location")
    else:
        coords = settings.location()
        if coords is None:
            return failure("location_not_set")
    lat, lon = coords

    try:
        got = refresh(lat, lon, key)
    except FetchError as exc:
        return failure(exc.reason)

    observed = got["observed"]
    now_at, now_hpa = observed["at_utc"], observed["hpa"]
    tz_min = int(observed.get("tz_min") or 0)
    result = assess(now_at, now_hpa, got["forecast"], past_series(now_at, th, got["place"]), th)
    out: dict[str, Any] = {
        "ok": True,
        "source": got["source"],
        "age_min": max(0, (storage.now_utc() - now_at) // 60),
        "now": {"hpa": round(now_hpa, 1), "at": storage.fmt_at(now_at, tz_min)},
        "level": result.level,
        "trend": result.trend,
        "falling_fast": result.falling_fast,
        "recent": None if result.recent_delta is None else
        {"delta_hpa": result.recent_delta, "over_h": result.recent_span_h},
        "ahead": None if result.ahead_delta is None else {
            "delta_hpa": result.ahead_delta, "over_h": result.ahead_span_h,
            "lowest_hpa": round(result.lowest_ahead[1], 1),
            "lowest_at": storage.fmt_at(result.lowest_ahead[0], tz_min),
        },
        "forecast": [{"at": storage.fmt_at(at, tz_min), "hpa": round(hpa, 1)}
                     for at, hpa in got["forecast"] if at <= now_at + hours_ahead * 3600],
        "thresholds": th.as_dict(),
    }
    if given and args.get("remember") is True:
        out["location_saved"] = settings.save_location(lat, lon)
    return out


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_pressure(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(settings, args), ensure_ascii=False)
        except Exception as exc:  # 道具は落とさずに理由を返す
            logger.debug("omonpakari: pressure tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_pressure
