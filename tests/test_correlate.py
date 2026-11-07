"""相関の道具の試験。母の数を持つこと、足りない時は印を返すことを検証する。"""

from __future__ import annotations

import json

from omonpakari import correlate, journal, storage

H = 3600
D = 86400


def _correlate(settings, **args):
    return json.loads(correlate.make_handler(settings)(args))


def _add(settings, label, at_utc, kind="symptom"):
    out = json.loads(journal.make_handler(settings)(
        {"action": "add", "kind": kind, "label": label, "at": storage.fmt_at(at_utc, 0)}))
    assert out["ok"] is True


def _pressure(now, low_spans=((15, 13), (8, 6)), days=20, step=3 * H, kind="observed"):
    """days 日前から今までの線。low_spans の間（何日前から何日前まで）だけ 1005、ほかは 1015。"""
    rows = []
    t = now - days * D
    while t <= now:
        low = any(now - a * D <= t < now - b * D for a, b in low_spans)
        rows.append({"kind": kind, "at_utc": t, "hpa": 1005.0 if low else 1015.0})
        t += step
    with storage.connect() as conn:
        storage.save_samples(conn, rows, now, "p1")


def test_数が足りない時は印を返す(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for k in range(3):
        _add(settings, "headache", now - 14 * D + k * 4 * H)
    out = _correlate(settings)
    assert out["ok"] is True and out["insufficient_data"] is True
    assert out["counts"]["entries_with_pressure"] == 3
    assert out["needed"] == {"entries_with_pressure": 5, "entry_days_with_pressure": 3,
                             "hours_with_pressure": 72}
    assert "lift" not in out


def test_一日に固まった記録だけでは比を言わない(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for k in range(6):                                # 重い日の数時間に6つ
        _add(settings, "headache", now - 7 * D + k * 30 * 60)
    out = _correlate(settings)
    assert out["insufficient_data"] is True and "lift" not in out
    assert out["counts"]["entries_with_pressure"] == 6 and out["counts"]["entry_days_with_pressure"] == 1


def test_変化の見えた記録が少ない時は下がりの割合を出さない(hermes, settings):
    now = hermes.clock.now
    _pressure(now, days=6, low_spans=((4, 3),))      # 線は6日前から
    for k in range(4):                                # 線の始まりの直後で、12時間前の気圧が見えない
        _add(settings, "headache", now - 6 * D + 1 * H + k * 2 * H + (k % 2) * D)
    _add(settings, "headache", now - 2 * D)
    _add(settings, "headache", now - 1 * D)
    out = _correlate(settings, window_h=12)
    assert out["insufficient_data"] is False
    assert out["entries"]["change_known"] < 5
    assert out["entries"]["falling_share"] is None and out["lift"]["falling"] is None
    assert out["lift"]["low"] is not None


def test_日数が少ない比には早い見えの印を付ける(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for day in (14, 13, 7, 3, 2):
        _add(settings, "headache", now - day * D)
    out = _correlate(settings)
    assert out["insufficient_data"] is False and out["small_sample"] is True
    for day in range(1, 20):
        _add(settings, "headache", now - day * D + 6 * H)
    assert _correlate(settings)["small_sample"] is False


def test_短い窓でも下がりの境に床を置く(hermes, settings):
    assert _correlate(settings, window_h=1)["settings"]["drop_hpa"] == 0.5
    assert _correlate(settings, drop_hpa=0.01)["settings"]["drop_hpa"] == 0.5
    assert _correlate(settings, drop_hpa=500)["settings"]["drop_hpa"] == 50.0


def test_気圧の溜めが無い時も印を返す(hermes, settings):
    now = hermes.clock.now
    for k in range(6):
        _add(settings, "headache", now - k * D)
    out = _correlate(settings)
    assert out["insufficient_data"] is True and out["counts"]["hours_with_pressure"] == 0


def test_母の数と出来事の割合と比を返す(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for base in (now - 14 * D, now - 7 * D):          # 低い間に3つずつ
        for k in range(3):
            _add(settings, "headache", base + k * 4 * H)
    _add(settings, "headache", now - 3 * D)          # ふつうの時に2つ
    _add(settings, "headache", now - 2 * D)
    _add(settings, "headache", now - 30 * D)         # 線の外（数に入らない）
    out = _correlate(settings)
    assert out["ok"] is True and out["insufficient_data"] is False
    counts = out["counts"]
    assert counts["entries"] == 9 and counts["entries_with_pressure"] == 8
    assert counts["hours"] == 60 * 24 + 1
    assert 470 <= counts["hours_with_pressure"] <= 482
    assert out["entries"]["in_low"] == 6
    assert out["entries"]["low_share"] == 0.75
    assert 0.15 < out["baseline"]["low_share"] < 0.25   # 母の数：低い時間はもともと2割ほど
    assert out["lift"]["low"] > 2.5
    assert out["settings"]["drop_hpa"] == 3.0            # 24時間で6を、12時間の窓に割り戻す
    assert out["mean_change_hpa"]["all_hours"] is not None
    assert out["by_label"][0]["label"] == "headache"
    assert "diagnos" not in json.dumps(out).lower()  # 医の見立ての文は付けない


def test_下がっている時間の印と比(hermes, settings):
    now = hermes.clock.now
    _pressure(now, low_spans=((15, 13), (8, 6), (4, 3)))
    # 下がり始め（15日前、8日前、4日前）から数時間後に出来事を置く
    for base in (now - 15 * D, now - 8 * D, now - 4 * D):
        for k in (1, 3, 5):
            _add(settings, "headache", base + k * H)
    out = _correlate(settings, window_h=12)
    assert out["entries"]["in_falling"] == 9
    assert out["entries"]["falling_share"] == 1.0
    assert out["baseline"]["falling_share"] < 0.1
    assert out["lift"]["falling"] > 10
    assert out["mean_change_hpa"]["before_entries"] < -5


def test_言葉で絞る(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for k in range(5):                                # どちらも3日にまたがる
        _add(settings, "neck", now - 15 * D + 1 * H + k * 11 * H)
        _add(settings, "headache", now - 3 * D + k * 11 * H)
    neck = _correlate(settings, label="Neck")
    assert neck["counts"]["entries"] == 5 and neck["entries"]["in_low"] == 5
    assert "by_label" not in neck
    both = _correlate(settings)
    assert {g["label"] for g in both["by_label"]} == {"neck", "headache"}


def test_薬や覚えは種類で選ぶ(hermes, settings):
    now = hermes.clock.now
    _pressure(now)
    for k in range(5):
        _add(settings, "tablet", now - 14 * D + k * 3 * H, kind="medication")
    assert _correlate(settings)["counts"]["entries"] == 0
    assert _correlate(settings, kind="medication")["counts"]["entries"] == 5
    assert _correlate(settings, kind="any")["counts"]["entries"] == 5
    assert _correlate(settings, kind="mood")["reason"] == "invalid_kind"


def test_実測だけに絞れる(hermes, settings):
    now = hermes.clock.now
    _pressure(now, days=10, kind="forecast")
    plain = _correlate(settings)
    assert plain["counts"]["forecast_points"] > 0 and plain["counts"]["hours_with_pressure"] > 72
    only = _correlate(settings, observed_only=True)
    assert only["counts"]["forecast_points"] == 0 and only["counts"]["hours_with_pressure"] == 0


def test_変な引数は誤りか範囲に収める(hermes, settings):
    assert _correlate(settings, drop_hpa=-1)["reason"] == "invalid_drop_hpa"
    assert _correlate(settings, drop_hpa="lots")["reason"] == "invalid_drop_hpa"
    out = _correlate(settings, days=10_000, window_h=0)
    assert out["settings"]["days"] == 365 and out["settings"]["window_h"] == 1
    hermes.db.broken = True
    assert _correlate(settings)["reason"] == "storage_unavailable"
