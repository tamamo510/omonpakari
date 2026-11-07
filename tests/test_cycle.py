"""生理の巡りの試験。不順でも単発の予定日を出さないこと、範囲で返すこと。"""

from __future__ import annotations

from datetime import datetime, timezone

from omonpakari import cycle, journal


def _at(day: str) -> str:
    return f"{day}T08:00+00:00"


def _mark(mark: str, day: str) -> dict:
    return cycle.run({"action": "mark", "mark": mark, "at": _at(day)})


def test_巡りを刻んで状態が返る():
    assert _mark("start", "2026-11-16")["ok"]
    assert _mark("end", "2026-11-21")["ok"]
    assert _mark("start", "2026-12-14")["ok"]
    assert _mark("end", "2026-12-19")["ok"]
    result = _mark("start", "2027-01-08")
    assert result["ok"]
    status = result
    assert status["cycles_observed"] == 2
    assert status["cycle_length_min"] == 25
    assert status["cycle_length_max"] == 28
    assert status["cycle_length_avg"] == 26.5
    assert status["last_start"] == "2027-01-08"
    assert status["days_since_start"] == 7


def test_次の窓は範囲で単発の日を出さない():
    _mark("start", "2026-11-16")
    _mark("start", "2026-12-14")
    _mark("start", "2027-01-08")
    status = cycle.run({"action": "status"})
    window = status["next_window"]
    assert window["earliest"] == "2027-02-02"   # 1/8 + 最短25日
    assert window["latest"] == "2027-02-05"     # 1/8 + 最長28日
    assert window["earliest"] != window["latest"]
    # 単発の予定日という欄は無い
    assert "next_date" not in status and "next_start" not in status


def test_巡りが1回だけでは窓を言わない():
    _mark("start", "2027-01-08")
    status = cycle.run({"action": "status"})
    assert status["insufficient_data"] is True
    assert "next_window" not in status


def test_時期は見立てとして返る():
    _mark("start", "2026-11-16")
    _mark("end", "2026-11-21")
    _mark("start", "2026-12-14")
    _mark("start", "2027-01-08")
    status = cycle.run({"action": "status"})
    # 2027-01-15 は最終開始から7日目、生理の長さの平均は6日 → 卵胞期の見立て
    assert status["phase"] == "follicular (estimate)"


def test_生理中は生理中と分かる():
    _mark("start", "2026-12-14")
    _mark("end", "2026-12-19")
    _mark("start", "2027-01-13")
    status = cycle.run({"action": "status"})
    assert status["days_since_start"] == 2
    assert "menstrual" in status["phase"]


def test_印を消せる():
    marked = _mark("start", "2027-01-08")
    mark_id = marked["marked"]["id"]
    assert cycle.run({"action": "delete", "id": mark_id})["ok"]
    assert cycle.run({"action": "status"})["periods_seen"] == 0


def test_未来の印は刻まない():
    result = _mark("start", "2027-02-01")
    assert result["ok"] is False
    assert result["reason"] == "at_in_future"


def test_全消しで巡りと暮らしも消える():
    _mark("start", "2027-01-08")
    journal.run({"action": "add", "kind": "note", "label": "memo"})
    assert journal.run({"action": "purge", "confirm": True})["ok"]
    assert cycle.run({"action": "status"})["periods_seen"] == 0
