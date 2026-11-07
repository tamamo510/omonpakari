"""暮らしの記憶の道具 omonpakari_life。

体調の数字とは別に、その人の習慣と普段の選びを短い言葉で覚える置き場。
「朝は普段これを飲む」「身体を温めたい時はこれ」のような、会話の持っていき方に
効く覚え。使う人が教えたことだけを置き、推測で作らない。
吸収の確かめは、ここにある代替（relates で結んだ覚え）を一緒に渡す。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_life"
MAX_TOPIC = 60
MAX_TEXT = 200
MAX_NOTES = 50

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "The person's own life notes: habits, routines and usual choices that should shape everyday "
        "suggestions (for example their usual non-caffeine morning drink, or what warms them up). "
        "action=set stores one note under a short topic, in the user's words, only when the user "
        "teaches it; relates optionally names a food, drink or supplement the note is an alternative "
        "to. action=list reads the notes back. action=delete removes one by topic. "
        "Never invent a note the user did not teach."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "list", "delete"],
                       "description": "What to do."},
            "topic": {"type": "string", "description": "Short topic in the user's words, e.g. "
                      "'morning drink' (max 60 characters). Required for set and delete."},
            "text": {"type": "string", "description": "The note itself, short (max 200 characters). "
                     "Required for set."},
            "relates": {"type": "string", "description": "Optional: a food, drink or supplement "
                        "this note is the person's alternative to, in their words."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _error(reason: str, **extra: Any) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _text(value: Any, limit: int, name: str) -> tuple[Optional[str], Optional[dict]]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _error(f"invalid_{name}")
    text = " ".join(value.split())
    if len(text) > limit:
        return None, _error(f"{name}_too_long", max=limit)
    return (text or None), None


def _note(row: tuple) -> dict:
    (_id, topic, text, relates, updated) = row
    out = {"topic": topic, "text": text, "updated": storage.fmt_utc(updated)}
    if relates:
        out["relates"] = relates
    return out


_COLUMNS = "id, topic, text, relates, updated_utc"


def load_notes(conn) -> list[dict]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM life_notes ORDER BY updated_utc").fetchall()
    return [_note(r) for r in rows]


def alternatives_for(conn, *labels: str) -> list[dict]:
    """ラベルに結ばれた代替の覚え（使う人の言葉のまま）。"""
    keys = {storage.label_key(label) for label in labels if label}
    if not keys:
        return []
    rows = conn.execute(
        "SELECT id, topic, text, relates, relates_key, updated_utc FROM life_notes "
        "WHERE relates_key IS NOT NULL").fetchall()
    return [_note((r[0], r[1], r[2], r[3], r[5])) for r in rows if r[4] in keys]


def _set(conn, args: dict) -> dict:
    topic, err = _text(args.get("topic"), MAX_TOPIC, "topic")
    if err:
        return err
    if not topic:
        return _error("topic_required")
    text, err = _text(args.get("text"), MAX_TEXT, "text")
    if err:
        return err
    if not text:
        return _error("text_required")
    relates, err = _text(args.get("relates"), 80, "relates")
    if err:
        return err
    count = conn.execute("SELECT COUNT(*) FROM life_notes").fetchone()[0]
    key = storage.label_key(topic)
    exists = conn.execute("SELECT 1 FROM life_notes WHERE topic_key = ?", (key,)).fetchone()
    if not exists and count >= MAX_NOTES:
        return _error("too_many_notes", max=MAX_NOTES)
    now = storage.now_utc()
    with conn:
        conn.execute(
            """
            INSERT INTO life_notes (topic, topic_key, text, relates, relates_key, created_utc, updated_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (topic_key) DO UPDATE SET topic = excluded.topic, text = excluded.text,
                relates = excluded.relates, relates_key = excluded.relates_key,
                updated_utc = excluded.updated_utc
            """,
            (topic, key, text, relates, storage.label_key(relates) if relates else None, now, now))
    return {"ok": True, "note": {"topic": topic, "text": text,
                                 **({"relates": relates} if relates else {})}}


def _list(conn, args: dict) -> dict:
    return {"ok": True, "notes": load_notes(conn)}


def _delete(conn, args: dict) -> dict:
    topic, err = _text(args.get("topic"), MAX_TOPIC, "topic")
    if err:
        return err
    if not topic:
        return _error("topic_required")
    with conn:
        cursor = conn.execute("DELETE FROM life_notes WHERE topic_key = ?",
                              (storage.label_key(topic),))
    if cursor.rowcount == 0:
        return _error("not_found", topic=topic)
    return {"ok": True, "deleted": topic}


_ACTIONS = {"set": _set, "list": _list, "delete": _delete}


def run(args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    action = _ACTIONS.get(args.get("action"))
    if action is None:
        return _error("invalid_action", allowed=list(_ACTIONS))
    try:
        with storage.connect() as conn:
            return action(conn, args)
    except Exception as exc:
        logger.debug("omonpakari: life storage error (%s)", type(exc).__name__)
        return _error("storage_unavailable")


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_life(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: life tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_life
