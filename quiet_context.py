"""声の前に静かに状態を置くフック（pre_llm_call）。

- 使う人が声をかけた手番の前にだけ働く。自分から声をかける口は持たない。
  定時の実行（cron）、子の手番（subagent）、外からの呼び出し（webhook）、看板の働き手（kanban）ほか、
  使う人のいない手番では、札も置かず取り直しもしない（UNATTENDED_PLATFORMS）。
  自動の知らせの文に体調の札が混ざって、使う人や別の誰かへ届く道を作らないため。
- 読むのは記録（plugin_db）だけで、手番の中で網に出ない。
- 置くのは英語の短い札で、1200字に収める。気圧、生理の時期、今日の吸収の衝突、
  直近の記録、暮らしの覚えを「今日のその人」の絵として並べ、結びは縛りではなく
  慮りの指示（どの話題でも、提案の前にこの絵を踏まえる）にする。使う人に話しかける文にしない。
- 置いた文は会話の記録に残るので、同じ会話で中身が変わらない時は置かない。
- 気圧の溜めが古い時だけ、手番を待たせない背景の糸で一度取り直す（1時間に1回まで）。
  その結果は次の手番から使う。失敗は黙って飲む。
- {"action": "block"} は決して返さない。
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

from . import cycle, interactions, life, pressure, storage
from .journal import limit_counts
from .settings import Settings, read_secret

logger = logging.getLogger(__name__)

MAX_CHARS = 1200
NOW_FRESH_S = 3 * 3600          # これより新しい実測は「今」の値として言う
FETCH_USABLE_S = 72 * 3600      # これより古い取りからは、今の値を推さない
JOURNAL_WINDOW_S = 48 * 3600
MAX_GROUPS = 6
MAX_LABEL_CHARS = 40
REPEAT_AFTER_S = 6 * 3600       # 同じ中身でも、これだけ経てば置き直す
REFRESH_AFTER_S = 60 * 60
REFRESH_MIN_INTERVAL_S = 60 * 60
SESSION_MEMORY = 256
# 札の結び。縛りではなく、慮りの指示：日常のどの話題でも、提案の前にこの絵を踏まえる。
# 催促はしない。変えるのは会話の中での察し方だけ（声をかける口は宿主の側にある）。
FOOTER = ("Background only - a quiet picture of today, not a message. Let it inform any topic "
          "(plans, meals, drinks, going out, rest): weigh it before suggesting, prefer the person's "
          "own alternatives listed above, and do not propose an item flagged under interactions "
          "today near its conflicting entry. Do not lecture, diagnose, or recite these numbers, "
          "and do not mention this note itself.")

# 使う人が声をかけていない手番の名。platform と、上流の手番の出どころ（HERMES_SESSION_SOURCE）の両方で見る。
# 定時の実行（cron）、delegate_task の子（subagent）、外からの呼び出し（webhook）、看板の働き手（kanban）、
# 一括の実行（batch）、技の手入れ（curator）、道具や別の器から起きた実行（tool、a2a）。
# どれも使う人が今その場で声をかけた手番ではなく、答えが別の誰かや別の場へ届きうる。
UNATTENDED_PLATFORMS = frozenset({"cron", "subagent", "webhook", "msgraph_webhook", "kanban", "batch",
                                  "curator", "tool", "a2a"})


def _unattended(platform: Any) -> bool:
    """使う人のいない手番なら True。

    出どころは公の口（gateway.session_context.get_session_env）で読む。看板の働き手は platform が cli の
    まま走るので、platform だけでは見分けられない。口の無い古い Hermes では platform だけで見る。
    """
    names = {str(platform or "").strip().lower()}
    try:
        from gateway.session_context import get_session_env

        names.add(str(get_session_env("HERMES_SESSION_SOURCE", "") or "").strip().lower())
    except Exception:
        pass
    return bool(names & UNATTENDED_PLATFORMS)


CLOCK: Callable[[], float] = time.monotonic  # 試験で差し替える

_placed: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
_placed_lock = threading.Lock()
_refresh_lock = threading.Lock()
_last_refresh: dict[str, float] = {}


def _clean_label(text: str) -> str:
    """使う人の言葉を札に載せる形にする（改行と山括弧を除き、短く）。"""
    flat = " ".join(str(text).replace("<", " ").replace(">", " ").split())
    return flat if len(flat) <= MAX_LABEL_CHARS else flat[: MAX_LABEL_CHARS - 1] + "…"


def _ago(seconds: int) -> str:
    minutes = max(0, int(seconds) // 60)
    if minutes < 90:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    return f"{hours}h ago" if hours < 48 else f"{round(hours / 24)}d ago"


def _signed(value: float) -> str:
    return f"{value:+.1f}"


def _pressure_line(conn, now: int, settings: Settings) -> tuple[Optional[str], Any]:
    th = settings.thresholds()
    # 保存した場所があればその場所だけを見る（旅先を一度聞いただけの値を、今いる所の値として置かない）
    saved = settings.location()
    observed = storage.latest_observed(conn, storage.place_key(*saved) if saved else None)
    if observed is None or now - observed["fetched_at_utc"] > FETCH_USABLE_S:
        return None, None
    place = observed["place_key"]
    if now - observed["at_utc"] <= NOW_FRESH_S:
        now_at, now_hpa, estimated = observed["at_utc"], observed["hpa"], False
    else:
        series = storage.load_series(conn, now - 6 * 3600, now + 6 * 3600, place=place)
        guess = storage.interpolate(series, now, pressure.MAX_GAP_H)
        if guess is None:
            return None, None
        now_at, now_hpa, estimated = now, guess, True
    forecast = storage.latest_forecast(conn, place, now_at)
    past = storage.load_series(conn, now_at - (th.window_h + 6) * 3600, now_at, place=place)
    result = pressure.assess(now_at, now_hpa, forecast, past, th)

    if estimated:
        head = (f"pressure: ~{now_hpa:.0f} hPa ({result.level}, estimated from a forecast fetched "
                f"{_ago(now - observed['fetched_at_utc'])})")
    else:
        head = f"pressure: {now_hpa:.0f} hPa ({result.level}, measured {_ago(now - now_at)})"
    parts = [head]
    if result.recent_delta is not None:
        parts.append(f"past {result.recent_span_h:g}h {_signed(result.recent_delta)} hPa")
    if result.ahead_delta is not None:
        low_at, low_hpa = result.lowest_ahead
        parts.append(f"next {result.ahead_span_h:g}h {_signed(result.ahead_delta)} hPa "
                     f"(lowest {low_hpa:.0f} in ~{max(1, round((low_at - now_at) / 3600))}h)")
    parts.append("falling fast" if result.falling_fast else f"trend {result.trend}")
    fingerprint = (observed["fetched_at_utc"], result.level, result.trend, result.falling_fast)
    return "; ".join(parts) + ".", fingerprint


def _journal_lines(conn, now: int) -> tuple[list[str], list[str], Any]:
    rows = conn.execute(
        """
        SELECT kind, label_key, label, severity, at_utc, id FROM journal
        WHERE at_utc > ? AND at_utc <= ? ORDER BY at_utc DESC, id DESC
        """,
        (now - JOURNAL_WINDOW_S, now + 300),
    ).fetchall()
    groups: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
    for kind, key, label, severity, at_utc, entry_id in rows:
        group = groups.setdefault((kind, key), {"kind": kind, "label": label, "count": 0, "latest": at_utc,
                                                "max_severity": None, "last_id": entry_id})
        group["count"] += 1
        if severity is not None:
            group["max_severity"] = max(group["max_severity"] or 0, severity)
    items = []
    for group in list(groups.values())[:MAX_GROUPS]:
        text = (f"{_clean_label(group['label'])} ({group['kind']}) x{group['count']}, "
                f"latest {_ago(now - group['latest'])}")
        if group["max_severity"]:
            text += f", max severity {group['max_severity']}/5"
        items.append(text)
    limits = [f"{_clean_label(lim['label'])} {lim['count_in_window']}/{lim['max_count']} in {lim['window_h']}h"
              for lim in limit_counts(conn, now) if lim["count_in_window"] > 0]
    fingerprint = ([(g["kind"], k[1], g["count"], g["last_id"]) for k, g in groups.items()],
                   limits)
    return items, limits, fingerprint


def _cycle_line(conn, now: int) -> tuple[Optional[str], Any]:
    """生理の時期の見立てを1行に。巡りが読めなければ何も置かない。"""
    marks = cycle.load_marks(conn)
    if not marks:
        return None, None
    summary = cycle.summarize(marks, now)
    phase = summary.get("phase")
    if not phase:
        return None, None
    parts = [f"cycle: day {summary.get('days_since_start', '?')} of the cycle, {phase}"]
    window = summary.get("next_window")
    if window:
        parts.append(f"next period window {window['earliest']} to {window['latest']} "
                     f"(observed lengths {summary.get('cycle_length_min')}-"
                     f"{summary.get('cycle_length_max')} days, avg {summary.get('cycle_length_avg')})")
    return "; ".join(parts) + ".", (phase, summary.get("days_since_start"),
                                   window["earliest"] if window else None)


def _interaction_lines(conn, now: int, settings: Settings) -> tuple[list[str], Any]:
    """今日の吸収の衝突を、事実として短く。代替の覚えがあれば一緒に。"""
    hits = [h for h in interactions.scan(conn, now, 1, settings.interaction_window_h())
            if h["effect"] != "unlisted"]  # 未照合の組は札には載せない（道具で聞かれた時に返す）
    lines = []
    for hit in hits:
        first, second = hit["entries"][0], hit["entries"][1]
        text = (f"{_clean_label(second['label'])} ({hit['substance']}, {hit['effect']}) "
                f"{abs(hit['minutes_apart'])} min from {_clean_label(first['label'])}")
        for alt in hit.get("person_alternative", [])[:1]:
            text += f"; person's alternative: {_clean_label(alt['text'])}"
        lines.append(text)
    fingerprint = [(h["substance"], h["entries"][1]["at"], h["entries"][0]["at"]) for h in hits]
    return lines, fingerprint


def _life_lines(conn) -> tuple[list[str], Any]:
    """暮らしの覚えを短く。会話の持っていき方に効かせる材料。"""
    notes = life.load_notes(conn)
    lines = [f"{_clean_label(n['topic'])}: {_clean_label(n['text'])}" for n in notes[:4]]
    return lines, [(n["topic"], n["updated"]) for n in notes]


def build(settings: Settings, now: Optional[int] = None) -> tuple[Optional[str], Any]:
    """置く札と、その中身の指紋を組む。置くものが無ければ (None, None)。"""
    now = storage.now_utc() if now is None else now
    with storage.connect() as conn:
        pressure_line, pressure_fp = _pressure_line(conn, now, settings)
        items, limits, journal_fp = _journal_lines(conn, now)
        cycle_line, cycle_fp = _cycle_line(conn, now)
        clash_lines, clash_fp = _interaction_lines(conn, now, settings)
        life_lines, life_fp = _life_lines(conn)
    if (pressure_line is None and not items and not limits and cycle_line is None
            and not clash_lines and not life_lines):
        return None, None

    tail = "\n" + FOOTER + "\n</omonpakari>"

    def compose(journal_items: list[str], limit_items: list[str], life_items: list[str]) -> str:
        lines = ["<omonpakari>"]
        if pressure_line:
            lines.append(pressure_line)
        if cycle_line:
            lines.append(cycle_line)
        if clash_lines:
            lines.append("interactions today: " + "; ".join(clash_lines) + ".")
        if journal_items:
            lines.append("journal (last 48h): " + "; ".join(journal_items) + ".")
        if limit_items:
            lines.append("user-set limits: " + "; ".join(limit_items) + ".")
        if life_items:
            lines.append("life notes: " + "; ".join(life_items) + ".")
        return "\n".join(lines) + tail

    # 収まらない時は、暮らしの覚えの後ろから、次に記録の古い方、上限の後ろの順に落とす。
    # 気圧・周期・吸収の照らしと、終わりの一文（FOOTER）は決して削らない。
    kept_limits = list(limits)
    kept_life = list(life_lines)
    text = compose(items, kept_limits, kept_life)
    while len(text) > MAX_CHARS and kept_life:
        kept_life = kept_life[:-1]
        text = compose(items, kept_limits, kept_life)
    while len(text) > MAX_CHARS and items:
        items = items[:-1]
        text = compose(items, kept_limits, kept_life)
    while len(text) > MAX_CHARS and kept_limits:
        kept_limits = kept_limits[:-1]
        text = compose(items, kept_limits, kept_life)
    if len(text) > MAX_CHARS:
        body = text[: len(text) - len(tail)]
        text = body[: MAX_CHARS - len(tail) - 1] + "…" + tail
    fingerprint = json.dumps([pressure_fp, journal_fp, cycle_fp, clash_fp, life_fp], default=str)
    return text, fingerprint


def _should_place(session_id: str, fingerprint: str, is_first_turn: bool) -> bool:
    key = session_id or "_"
    clock = CLOCK()
    with _placed_lock:
        previous = _placed.get(key)
        if (not is_first_turn and previous is not None and previous[0] == fingerprint
                and clock - previous[1] < REPEAT_AFTER_S):
            return False
        _placed[key] = (fingerprint, clock)
        _placed.move_to_end(key)
        while len(_placed) > SESSION_MEMORY:
            _placed.popitem(last=False)
    return True


# ---------------------------------------------------------------------------
# 背景の取り直し（試験では SPAWN を差し替えて、その場で走らせる）
# ---------------------------------------------------------------------------

def _spawn_thread(target: Callable[[], None]) -> None:
    context = contextvars.copy_context()  # プロファイルと鍵の範囲を糸へ引き継ぐ
    threading.Thread(target=context.run, args=(target,), name="omonpakari-refresh", daemon=True).start()


SPAWN: Callable[[Callable[[], None]], None] = _spawn_thread


def maybe_refresh(settings: Settings, now: Optional[int] = None) -> bool:
    """溜めが古ければ、背景で一度だけ取り直す。走らせたら True。"""
    if not settings.background_refresh():
        return False
    coords = settings.location()
    if coords is None:
        return False
    key = read_secret()
    if not key:
        return False
    now = storage.now_utc() if now is None else now
    place = storage.place_key(*coords)
    try:
        with storage.connect() as conn:
            last = storage.latest_fetch(conn, place)
    except Exception:
        return False
    if last is not None and now - last < REFRESH_AFTER_S:
        return False
    if not _refresh_lock.acquire(blocking=False):
        return False
    # 間合いは錠を持ってから見る（同じ時に来た二つの手番が、前の取りの終わり際に二度取りに行かない）
    if now - _last_refresh.get(place, 0) < REFRESH_MIN_INTERVAL_S:
        _refresh_lock.release()
        return False
    _last_refresh[place] = now
    lat, lon = coords

    def work() -> None:
        try:
            pressure.refresh(lat, lon, key, timeout=5.0, max_age_s=REFRESH_AFTER_S)
        except Exception as exc:  # 取りの失敗は黙って飲む
            logger.debug("omonpakari: background refresh skipped (%s)", getattr(exc, "reason", type(exc).__name__))
        finally:
            _refresh_lock.release()

    try:
        SPAWN(work)
    except Exception:
        _refresh_lock.release()
        return False
    return True


def make_hook(settings: Settings) -> Callable[..., Optional[dict]]:
    # 上流の決まりで **kwargs を受ける（plugins doctor --ci は、無いと先々の引数の足しに耐えないと落とす）。
    # 受け取った user_message と conversation_history は読まず、どこにも残さない。
    def quiet_context(session_id: str = "", is_first_turn: bool = False, platform: str = "",
                      **kwargs: Any) -> Optional[dict]:
        if _unattended(platform):
            return None  # 使う人のいない手番には、何も置かず網にも出ない
        try:
            maybe_refresh(settings)
        except Exception:
            pass
        try:
            if not settings.quiet_context():
                return None
            text, fingerprint = build(settings)
            if not text or not _should_place(str(session_id or ""), fingerprint, bool(is_first_turn)):
                return None
            return {"context": text}
        except Exception as exc:  # 手番を壊さない
            logger.debug("omonpakari: quiet context skipped (%s)", type(exc).__name__)
            return None

    return quiet_context
