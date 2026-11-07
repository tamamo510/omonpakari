"""部品：気圧の波。

気圧の今の位置と、ここまでの下がり方、これからの向きを、波の状態として返す。
部品の約束は1つだけ：state(settings, conn, now) で、自分の波の状態か None を返す。
"""

from __future__ import annotations

from typing import Optional

from . import pressure, storage
from .settings import Settings

NAME = "pressure"
FETCH_USABLE_S = 72 * 3600
NOW_FRESH_S = 3 * 3600


def state(settings: Settings, conn, now: int) -> Optional[dict]:
    th = settings.thresholds()
    saved = settings.location()
    observed = storage.latest_observed(conn, storage.place_key(*saved) if saved else None)
    if observed is None or now - observed["fetched_at_utc"] > FETCH_USABLE_S:
        return None
    place = observed["place_key"]
    if now - observed["at_utc"] <= NOW_FRESH_S:
        now_hpa = observed["hpa"]
    else:
        series = storage.load_series(conn, now - 6 * 3600, now + 6 * 3600, place=place)
        now_hpa = storage.interpolate(series, now, pressure.MAX_GAP_H)
    if now_hpa is None:
        return None
    forecast = storage.latest_forecast(conn, place, now)
    past = storage.load_series(conn, now - (th.window_h + 6) * 3600, now, place=place)
    result = pressure.assess(now, now_hpa, forecast, past, th)
    out = {"level": result.level, "trend": result.trend, "falling_fast": result.falling_fast}
    if result.ahead_delta is not None:
        out["ahead"] = ("falling" if result.ahead_delta < -0.5
                        else "rising" if result.ahead_delta > 0.5 else "steady")
        out["ahead_hours"] = result.ahead_span_h
    if result.recent_delta is not None:
        out["recent"] = ("fell" if result.recent_delta < -0.5
                         else "rose" if result.recent_delta > 0.5 else "steady")
    return out
