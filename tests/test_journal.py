"""記録の道具の試験。通信には出ない。"""

from __future__ import annotations

import json

import pytest

from omonpakari import journal, storage

H = 3600


def _log(settings, **args):
    return json.loads(journal.make_handler(settings)(args))


def _add(settings, **args):
    return _log(settings, action="add", **args)


def test_記録して見返せる(hermes, settings):
    out = _add(settings, kind="symptom", label="headache", severity=3, note="after a long screen day")
    assert out["ok"] is True
    entry = out["entry"]
    assert entry["label"] == "headache" and entry["severity"] == 3 and entry["kind"] == "symptom"
    listed = _log(settings, action="list")
    assert listed["ok"] is True and listed["shown"] == 1 and listed["matching"] == 1
    assert listed["entries"][0]["id"] == entry["id"]
    assert listed["entries"][0]["note"] == "after a long screen day"


def test_決まった名を持たず使う人の言葉で束ねる(hermes, settings):
    _add(settings, kind="symptom", label="Stiff  Neck")
    _add(settings, kind="symptom", label="ｓｔｉｆｆ neck ")
    _add(settings, kind="symptom", label="something new I made up")
    listed = _log(settings, action="list", label="stiff neck")
    assert listed["matching"] == 2
    assert storage.label_key("Stiff  Neck") == storage.label_key("ｓｔｉｆｆ neck ") == "stiff neck"


def test_時差の混ざった時刻を正しく数える(hermes, settings):
    now = hermes.clock.now
    # 同じ瞬間を、時差の違う2つの書き方で残す
    same_moment_tokyo = storage.fmt_at(now - 2 * H, 9 * 60)
    same_moment_utc = storage.fmt_at(now - 2 * H, 0)
    a = _add(settings, kind="medication", label="tablet", at=same_moment_tokyo)
    b = _add(settings, kind="medication", label="tablet", at=same_moment_utc.replace("+00:00", "Z"))
    assert a["entry"]["at"].endswith("+09:00") and b["entry"]["at"].endswith("+00:00")
    # 文字の並びでは前後が逆に見えても、時刻の値で絞る
    since = storage.fmt_at(now - 3 * H, -5 * 60)
    assert _log(settings, action="list", since=since)["matching"] == 2
    later = storage.fmt_at(now - 1 * H, 9 * 60)
    assert _log(settings, action="list", since=later)["matching"] == 0


def test_上限の窓は時差によらず数える(hermes, settings):
    now = hermes.clock.now
    _log(settings, action="set_limit", label="tablet", max_count=3, window_h=24)
    _add(settings, kind="medication", label="tablet", at=storage.fmt_at(now - 23 * H, 9 * 60))
    _add(settings, kind="medication", label="tablet", at=storage.fmt_at(now - 25 * H, -8 * 60))
    out = _add(settings, kind="medication", label="Tablet")
    assert out["limit"] == {"label": "tablet", "count_in_window": 2, "max_count": 3, "window_h": 24}


def test_薬の上限は決めた時だけ数として添える(hermes, settings):
    out = _add(settings, kind="medication", label="supplement", dose="1 capsule")
    assert "limit" not in out
    assert "limits" not in _log(settings, action="list")
    _log(settings, action="set_limit", label="supplement", max_count=1)
    listed = _log(settings, action="list")
    assert listed["limits"] == [{"label": "supplement", "count_in_window": 1, "max_count": 1, "window_h": 24}]
    # 上限に届いても、警告の文は作らない（数だけ）
    flat = json.dumps(listed).lower()
    assert "warn" not in flat and "should" not in flat


def test_上限を外す(hermes, settings):
    _log(settings, action="set_limit", label="supplement", max_count=2)
    out = _log(settings, action="set_limit", label="supplement")
    assert out["cleared"] is True
    assert "limits" not in _log(settings, action="list")


def test_気圧を溜めから添える(hermes, settings):
    now = hermes.clock.now
    with storage.connect() as conn:
        storage.save_samples(conn, [{"kind": "observed", "at_utc": now - 3600, "hpa": 1004.0}], now, "p1")
    near = _add(settings, kind="symptom", label="headache", at=storage.fmt_at(now - 3000, 0))
    assert near["entry"]["pressure_hpa"] == 1004.0
    far = _add(settings, kind="symptom", label="headache", at=storage.fmt_at(now - 6 * 3600, 0))
    assert "pressure_hpa" not in far["entry"]
    assert hermes.http.calls == []  # 書く時に通信へは出ない


@pytest.mark.parametrize("field, value, reason", [
    ("label", "x" * 81, "label_too_long"),
    ("note", "y" * 281, "note_too_long"),
    ("dose", "z" * 41, "dose_too_long"),
])
def test_長すぎる言葉は切らずに誤りを返す(hermes, settings, field, value, reason):
    args = {"kind": "note", "label": "ok", field: value}
    out = _add(settings, **args)
    assert out["ok"] is False and out["reason"] == reason
    assert _log(settings, action="list")["matching"] == 0


@pytest.mark.parametrize("severity", [0, 6, 2.5, "strong", True])
def test_強さは1から5の整数だけ(hermes, settings, severity):
    assert _add(settings, kind="symptom", label="ache", severity=severity)["reason"] == "invalid_severity"


def test_種類と言葉は要る(hermes, settings):
    assert _add(settings, kind="feeling", label="ache")["reason"] == "invalid_kind"
    assert _add(settings, kind="symptom", label="   ")["reason"] == "label_required"
    assert _add(settings, kind="symptom")["reason"] == "label_required"


def test_先の時刻と読めない時刻は拒む(hermes, settings):
    now = hermes.clock.now
    assert _add(settings, kind="note", label="x", at=storage.fmt_at(now + 2 * H, 0))["reason"] == "at_in_future"
    assert _add(settings, kind="note", label="x", at="yesterday-ish")["reason"] == "invalid_at"


def test_消す(hermes, settings):
    entry_id = _add(settings, kind="note", label="slept badly")["entry"]["id"]
    assert _log(settings, action="delete", id=entry_id) == {"ok": True, "deleted": entry_id}
    assert _log(settings, action="delete", id=entry_id)["reason"] == "not_found"
    assert _log(settings, action="delete")["reason"] == "id_required"


def test_全部消すには確かめが要る(hermes, settings):
    _add(settings, kind="note", label="a")
    _log(settings, action="set_limit", label="b", max_count=2)
    assert _log(settings, action="purge")["reason"] == "confirm_required"
    assert _log(settings, action="list")["matching"] == 1
    out = _log(settings, action="purge", confirm=True)
    assert out["erased"]["journal"] == 1 and out["erased"]["med_limits"] == 1
    assert _log(settings, action="list")["matching"] == 0


def test_開き直しても残る(hermes, settings):
    _add(settings, kind="symptom", label="dizzy")
    import sqlite3
    rows = sqlite3.connect(hermes.db.path).execute("SELECT label FROM journal").fetchall()
    assert rows == [("dizzy",)]
    assert _log(settings, action="list")["entries"][0]["label"] == "dizzy"
    assert hermes.db.opened == 2  # 道具を呼ぶたびに開いて、閉じてから返している


def test_見返しは新しい順で数を絞れる(hermes, settings):
    now = hermes.clock.now
    for i in range(5):
        _add(settings, kind="note", label=f"n{i}", at=storage.fmt_at(now - (5 - i) * H, 0))
    listed = _log(settings, action="list", limit=2)
    assert [e["label"] for e in listed["entries"]] == ["n4", "n3"]
    assert listed["matching"] == 5 and listed["shown"] == 2
    assert _log(settings, action="list", kind="symptom")["matching"] == 0


def test_知らない手は誤り(hermes, settings):
    assert _log(settings, action="remind")["reason"] == "invalid_action"
    assert json.loads(journal.make_handler(settings)("not a dict"))["reason"] == "invalid_action"


def test_置き場が壊れても例外を投げない(hermes, settings):
    hermes.db.broken = True
    assert _add(settings, kind="note", label="x") == {"ok": False, "reason": "storage_unavailable"}
