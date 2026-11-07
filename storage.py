"""記録の置き場と時刻の扱い。

記録は本家の plugin_db（<hermes home>/plugin-data/omonpakari/data.db）に置く。
プラグインの家の中には一文字も書かない（update と remove で消えるため）。

時刻は UTC の epoch 秒（整数）と、その時の時差の分（tz_min）の2つで持つ。
文字の並びで時刻を比べない。
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterator, Optional

PLUGIN_NAME = "omonpakari"

# 予報と実測の溜めを残す長さ。相関は最長365日を見るので、それより少し長く残す。
SAMPLE_RETENTION_DAYS = 400

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('symptom', 'medication', 'supplement', 'food', 'note')),
        label TEXT NOT NULL,
        label_key TEXT NOT NULL,
        severity INTEGER CHECK (severity IS NULL OR severity BETWEEN 1 AND 5),
        dose TEXT,
        note TEXT,
        at_utc INTEGER NOT NULL,
        tz_min INTEGER NOT NULL,
        pressure_hpa REAL,
        created_utc INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS journal_at ON journal (at_utc)",
    "CREATE INDEX IF NOT EXISTS journal_label ON journal (label_key, at_utc)",
    """
    CREATE TABLE IF NOT EXISTS med_limits (
        label_key TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        max_count INTEGER NOT NULL CHECK (max_count >= 1),
        window_h INTEGER NOT NULL CHECK (window_h >= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pressure_samples (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('observed', 'forecast')),
        at_utc INTEGER NOT NULL,
        hpa REAL NOT NULL,
        temp_c REAL,
        humidity REAL,
        fetched_at_utc INTEGER NOT NULL,
        place_key TEXT NOT NULL,
        tz_min INTEGER NOT NULL DEFAULT 0,
        UNIQUE (kind, place_key, at_utc)
    )
    """,
    "CREATE INDEX IF NOT EXISTS pressure_at ON pressure_samples (at_utc)",
    """
    CREATE TABLE IF NOT EXISTS cycle_marks (
        id INTEGER PRIMARY KEY,
        mark TEXT NOT NULL CHECK (mark IN ('start', 'end')),
        at_utc INTEGER NOT NULL,
        tz_min INTEGER NOT NULL,
        note TEXT,
        created_utc INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS cycle_marks_at ON cycle_marks (at_utc)",
    """
    CREATE TABLE IF NOT EXISTS life_notes (
        id INTEGER PRIMARY KEY,
        topic TEXT NOT NULL,
        topic_key TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL,
        relates TEXT,
        relates_key TEXT,
        created_utc INTEGER NOT NULL,
        updated_utc INTEGER NOT NULL
    )
    """,
)

_JOURNAL_KINDS_SQL = "'symptom', 'medication', 'supplement', 'food', 'note'"


def _migrate_journal_kinds(conn: sqlite3.Connection) -> None:
    """旧い台（種類が3つ）の journal があれば、記録を保ったまま種類を広げた台へ移す。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'journal'").fetchone()
    if not row or not row[0] or "'supplement'" in row[0]:
        return
    with conn:
        conn.execute(
            f"""
            CREATE TABLE journal_new (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ({_JOURNAL_KINDS_SQL})),
                label TEXT NOT NULL,
                label_key TEXT NOT NULL,
                severity INTEGER CHECK (severity IS NULL OR severity BETWEEN 1 AND 5),
                dose TEXT,
                note TEXT,
                at_utc INTEGER NOT NULL,
                tz_min INTEGER NOT NULL,
                pressure_hpa REAL,
                created_utc INTEGER NOT NULL
            )
            """)
        conn.execute("INSERT INTO journal_new SELECT * FROM journal")
        conn.execute("DROP TABLE journal")
        conn.execute("ALTER TABLE journal_new RENAME TO journal")
        conn.execute("CREATE INDEX IF NOT EXISTS journal_at ON journal (at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS journal_label ON journal (label_key, at_utc)")


# ---------------------------------------------------------------------------
# 時計（試験で差し替える）
# ---------------------------------------------------------------------------

def now_utc() -> int:
    """今の UTC の epoch 秒。"""
    return int(time.time())


def user_zone() -> Optional[tzinfo]:
    """使う人が Hermes に据えた時の帯（config.yaml の上の段の timezone、IANA の名）。

    据わっていない時、名が読めない時、Hermes の外では None（この機械の地の時刻を使う）。
    機械が UTC の貸し部屋で動き、使う人は別の帯にいる時に、素の時刻を何時間もずらして刻まないため。
    読むのは公の口（hermes_cli.config）だけにする（カタログの決まり9：私の口に頼らない）。
    """
    try:
        from hermes_cli.config import load_config_readonly

        name = (load_config_readonly() or {}).get("timezone")
    except Exception:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name.strip())
    except Exception:  # 名の誤りや、帯の資料の無い器
        return None


def local_tz_min(at_utc: Optional[int] = None) -> int:
    """その時刻の、使う人の帯の時差（分）。帯が据わっていなければ、この機械の地の時差。"""
    stamp = now_utc() if at_utc is None else at_utc
    moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
    zone = user_zone()
    offset = (moment.astimezone(zone) if zone is not None else moment.astimezone()).utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def parse_at(text: str) -> tuple[int, int]:
    """ISO 8601 の文字を (UTC の epoch 秒, 時差の分) に解く。

    時差の無い文字は、使う人の帯の時刻として読む（帯が据わっていなければ、この機械の地の時刻）。
    読めない時は ValueError。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty timestamp")
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        zone = user_zone()
        parsed = parsed.replace(tzinfo=zone) if zone is not None else parsed.astimezone()
    offset = parsed.utcoffset() or timedelta(0)
    return int(parsed.timestamp()), int(offset.total_seconds() // 60)


def fmt_at(at_utc: int, tz_min: int = 0) -> str:
    """(epoch 秒, 時差の分) を、時差つきの ISO 8601 の文字にする。"""
    zone = timezone(timedelta(minutes=int(tz_min)))
    return datetime.fromtimestamp(int(at_utc), tz=zone).isoformat(timespec="minutes")


def fmt_utc(at_utc: int) -> str:
    """UTC の ISO 8601（Z 付き）。"""
    return datetime.fromtimestamp(int(at_utc), tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ---------------------------------------------------------------------------
# 名の束ね
# ---------------------------------------------------------------------------

_SPACES = re.compile(r"\s+")


def label_key(label: str) -> str:
    """使う人の言葉を、束ねるための鍵にそろえる（全角半角、大小、空白の連なり）。"""
    text = unicodedata.normalize("NFKC", label or "")
    return _SPACES.sub(" ", text).strip().casefold()


def place_key(lat: float, lon: float) -> str:
    """座標を、線を場所ごとに見分けるための短い印に変える。座標そのものは記録に置かない。"""
    digest = hashlib.sha256(f"{round(lat, 2):.2f},{round(lon, 2):.2f}".encode("ascii")).hexdigest()
    return digest[:12]


# ---------------------------------------------------------------------------
# 記録の開き方
# ---------------------------------------------------------------------------

def _open() -> sqlite3.Connection:
    # 本家の決まりの置き場。関数の中で取り込むのは、Hermes の外の試験で差し替えるため。
    from plugins.plugin_storage import plugin_db

    conn = plugin_db(PLUGIN_NAME)
    _migrate_journal_kinds(conn)
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()
    return conn


@contextlib.contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """記録を開き、必ず閉じる。書き込みの区切りは呼ぶ側が `with conn:` で持つ。"""
    conn = _open()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 気圧の溜め
# ---------------------------------------------------------------------------

def save_samples(conn: sqlite3.Connection, rows: list[dict], fetched_at: int, place: str) -> int:
    """実測と予報の点を溜める。同じ場所と時刻の点は、新しい取りで置き換える。"""
    saved = 0
    with conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO pressure_samples
                    (kind, at_utc, hpa, temp_c, humidity, fetched_at_utc, place_key, tz_min)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (kind, place_key, at_utc) DO UPDATE SET
                    hpa = excluded.hpa, temp_c = excluded.temp_c, humidity = excluded.humidity,
                    fetched_at_utc = excluded.fetched_at_utc, tz_min = excluded.tz_min
                """,
                (row["kind"], int(row["at_utc"]), float(row["hpa"]), row.get("temp_c"),
                 row.get("humidity"), int(fetched_at), place, int(row.get("tz_min") or 0)),
            )
            saved += 1
        cutoff = int(fetched_at) - SAMPLE_RETENTION_DAYS * 86400
        conn.execute("DELETE FROM pressure_samples WHERE at_utc < ?", (cutoff,))
    return saved


def latest_fetch(conn: sqlite3.Connection, place: Optional[str] = None) -> Optional[int]:
    """いちばん新しい実測の取りの時刻（UTC 秒）。"""
    if place is None:
        row = conn.execute(
            "SELECT MAX(fetched_at_utc) FROM pressure_samples WHERE kind = 'observed'").fetchone()
    else:
        row = conn.execute(
            "SELECT MAX(fetched_at_utc) FROM pressure_samples WHERE kind = 'observed' AND place_key = ?",
            (place,)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def load_series(conn: sqlite3.Connection, start: int, end: int, observed_only: bool = False,
                place: Optional[str] = None) -> list[tuple[int, float, str]]:
    """[start, end] の気圧の線を、時刻の順に (at_utc, hpa, kind) で返す。

    同じ時刻に実測があれば実測を、無ければいちばん新しく取った予報を使う。
    place を渡せばその場所だけ。渡さなければ場所をまたぎ、その時に新しく取った値が勝つ
    （相関では、使う人がその時いた場所の気圧を見るため）。
    """
    sql = "SELECT kind, at_utc, hpa, fetched_at_utc FROM pressure_samples WHERE at_utc BETWEEN ? AND ?"
    params: list = [int(start), int(end)]
    if place is not None:
        sql += " AND place_key = ?"
        params.append(place)
    rows = conn.execute(sql + " ORDER BY at_utc, fetched_at_utc", params).fetchall()
    # 時刻ごとに (実測が先, 新しい取りが先) の順位でいちばん上の点を残す
    best: dict[int, tuple[tuple[int, int], float, str]] = {}
    for kind, at_utc, hpa, fetched in rows:
        if observed_only and kind != "observed":
            continue
        rank = (1 if kind == "observed" else 0, int(fetched))
        current = best.get(int(at_utc))
        if current is None or rank >= current[0]:
            best[int(at_utc)] = (rank, float(hpa), kind)
    return [(at, hpa, kind) for at, (_, hpa, kind) in sorted(best.items())]


def latest_forecast(conn: sqlite3.Connection, place: Optional[str], after: int) -> list[tuple[int, float]]:
    """その場所（None ならどこでも）の、いちばん新しい取りの予報のうち after より先の点。"""
    where, params = ("kind = 'forecast'", ())
    if place is not None:
        where, params = ("kind = 'forecast' AND place_key = ?", (place,))
    row = conn.execute(
        f"SELECT fetched_at_utc, place_key FROM pressure_samples WHERE {where} "
        "ORDER BY fetched_at_utc DESC LIMIT 1", params).fetchone()
    if not row:
        return []
    points = conn.execute(
        """
        SELECT at_utc, hpa FROM pressure_samples
        WHERE kind = 'forecast' AND place_key = ? AND fetched_at_utc = ? AND at_utc > ?
        ORDER BY at_utc
        """,
        (row[1], int(row[0]), int(after)),
    ).fetchall()
    return [(int(at), float(hpa)) for at, hpa in points]


def latest_observed(conn: sqlite3.Connection, place: Optional[str] = None) -> Optional[dict]:
    """いちばん新しく取った実測の点。"""
    sql = ("SELECT at_utc, hpa, temp_c, humidity, fetched_at_utc, place_key, tz_min "
           "FROM pressure_samples WHERE kind = 'observed'")
    params: tuple = ()
    if place is not None:
        sql += " AND place_key = ?"
        params = (place,)
    row = conn.execute(sql + " ORDER BY fetched_at_utc DESC, at_utc DESC LIMIT 1", params).fetchone()
    if not row:
        return None
    return {"at_utc": int(row[0]), "hpa": float(row[1]), "temp_c": row[2], "humidity": row[3],
            "fetched_at_utc": int(row[4]), "place_key": row[5], "tz_min": int(row[6] or 0)}


def interpolate(series: list[tuple[int, float, str]], t: int, max_gap_h: float = 6.0) -> Optional[float]:
    """線の上の時刻 t の気圧を、隣の2点の間を線でつないで求める。点の間が開きすぎる所は None。"""
    if not series:
        return None
    if t < series[0][0] or t > series[-1][0]:
        return None
    lo, hi = 0, len(series) - 1
    while lo < hi:  # t 以上の最初の点を探す
        mid = (lo + hi) // 2
        if series[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    right = series[lo]
    if right[0] == t:
        return right[1]
    left = series[lo - 1]
    gap = right[0] - left[0]
    if gap > max_gap_h * 3600:
        return None
    share = (t - left[0]) / gap
    return left[1] + (right[1] - left[1]) * share
