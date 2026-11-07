"""波の層の試験。症状の寄せ引きの向きと、合成の形を見る。"""

from __future__ import annotations

from omonpakari import journal, waves


def _symptom(label: str, day_offset: int, severity: int = 3):
    from datetime import datetime, timedelta, timezone
    base = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc)
    at = (base - timedelta(days=day_offset)).strftime("%Y-%m-%dT%H:%M+00:00")
    result = journal.run({"action": "add", "kind": "symptom", "label": label,
                          "severity": severity, "at": at})
    assert result["ok"], result


def test_症状の波の向きが出る(settings):
    # 先週2回、今週5回 → 寄せている
    for d in (8, 10):
        _symptom("headache", d)
    for d in (0, 1, 2, 3, 5):
        _symptom("headache", d)
    out = waves.compose(settings)
    symptom = out["waves"]["symptoms"]["waves"][0]
    assert symptom["label"] == "headache"
    assert symptom["direction"] == "more present this week"
    assert symptom["this_week"] == 5 and symptom["last_week"] == 2


def test_少ない症状は波にしない(settings):
    _symptom("itch", 1)
    out = waves.compose(settings)
    assert out["waves"]["symptoms"] is None


def test_部品は1つずつ読める(settings):
    for d in (8, 10):
        _symptom("headache", d)
    for d in (0, 1, 2, 3, 5):
        _symptom("headache", d)
    out = waves.run(settings, {"part": "symptoms"})
    assert list(out["waves"]) == ["symptoms"]
    assert waves.run(settings, {"part": "無い部品"})["reason"] == "invalid_part"


def test_眠りと鉄の部品(settings):
    from omonpakari import journal
    journal.run({"action": "add", "kind": "note", "label": "寝不足",
                 "at": "2027-01-15T07:00+00:00"})
    journal.run({"action": "add", "kind": "supplement", "label": "iron supplement",
                 "at": "2027-01-15T06:00+00:00"})
    out = waves.compose(settings)
    assert out["waves"]["sleep"]["mentions_this_week"] == 1
    assert out["waves"]["nutrition"]["iron"]["entries_this_week"] == 1


def test_通知ではないという注記が付く(settings):
    out = waves.compose(settings)
    assert "never as a notification" in out["note"]
