"""設定と鍵の読み方。

設定は ctx.get_config（plugins.entries.omonpakari.settings.<鍵>）から読む。
鍵は上流の決まりどおり agent.secret_scope.get_secret で読み、os.environ を直に読まない。
座標の既定値は持たない。使う人が設定か道具の引数で渡した時だけ使う。
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

API_KEY_NAME = "OPENWEATHER_API_KEY"


@dataclass(frozen=True)
class Thresholds:
    """判じの境目。どれも使う人が設定で変えられる。"""

    low_hpa: float = 1010.0
    very_low_hpa: float = 1003.0
    high_hpa: float = 1020.0
    drop_hpa: float = 6.0
    window_h: int = 24

    def as_dict(self) -> dict:
        return asdict(self)


def read_secret(name: str = API_KEY_NAME) -> Optional[str]:
    """鍵を上流の道から読む。読めない時（範囲の外の呼び出しを含む）は None。値は外へ出さない。"""
    try:
        from agent.secret_scope import get_secret
    except Exception:  # Hermes の外では鍵の道が無い
        return None
    try:
        value = get_secret(name)
    except Exception as exc:  # 多重の器で範囲が据わっていない時など
        logger.debug("omonpakari: secret lookup unavailable (%s)", type(exc).__name__)
        return None
    if not value or not str(value).strip():
        return None
    return str(value).strip()


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_coordinates(lat: Any, lon: Any) -> Optional[tuple[float, float]]:
    """緯度と経度が数として正しい範囲にある時だけ (lat, lon) を返す。"""
    la, lo = _as_float(lat), _as_float(lon)
    if la is None or lo is None:
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return None
    return la, lo


class Settings:
    """ctx を閉じ込めて、道具とフックの中で設定を読む。register の中では読まない。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self._ctx.get_config(key, default)
        except Exception:
            return default

    def _float(self, key: str, default: float) -> float:
        value = _as_float(self.get(key, default))
        return default if value is None else value

    def _int(self, key: str, default: int, low: int, high: int) -> int:
        value = _as_float(self.get(key, default))
        if value is None:
            return default
        return max(low, min(high, int(round(value))))

    def _bool(self, key: str, default: bool) -> bool:
        value = self.get(key, default)
        return value if isinstance(value, bool) else default

    def thresholds(self) -> Thresholds:
        base = Thresholds()
        return Thresholds(
            low_hpa=self._float("low_hpa", base.low_hpa),
            very_low_hpa=self._float("very_low_hpa", base.very_low_hpa),
            high_hpa=self._float("high_hpa", base.high_hpa),
            drop_hpa=abs(self._float("drop_hpa", base.drop_hpa)) or base.drop_hpa,
            window_h=self._int("window_h", base.window_h, 3, 72),
        )

    def location(self) -> Optional[tuple[float, float]]:
        return valid_coordinates(self.get("latitude"), self.get("longitude"))

    def save_location(self, lat: float, lon: float) -> bool:
        """使う人が頼んだ時だけ、座標を小数2桁（約1km）に丸めて設定に残す。"""
        try:
            self._ctx.set_config("latitude", round(lat, 2))
            self._ctx.set_config("longitude", round(lon, 2))
            return True
        except Exception as exc:
            logger.debug("omonpakari: could not save location (%s)", type(exc).__name__)
            return False

    def interaction_window_h(self) -> float:
        """吸収の照らしで「近い」と見る間合い（時間）。使う人が設定で変えられる。"""
        value = _as_float(self.get("interaction_window_h", 2.0))
        if value is None:
            return 2.0
        return max(0.5, min(12.0, value))

    def quiet_context(self) -> bool:
        return self._bool("quiet_context", True)

    def first_word_turns(self) -> bool:
        """宿主が自分で起きる定時の回（cron）でも札を置くか。本人が開く設定。既定は切。

        朝の回のように、宿主が決まった時刻に自分で起きて一声かける手番では、
        今日の絵が載らないと慮りの一声にならない。ただし自動の知らせに体調の札が
        混ざる道を作らないため、既定は切で、本人が自分で開いた時だけ cron を通す。
        """
        return self._bool("first_word_turns", False)

    def background_refresh(self) -> bool:
        return self._bool("background_refresh", True)
