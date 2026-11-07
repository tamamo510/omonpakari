"""疑って検証した所の試験。時の帯、使う人のいない回、求心路の終わりの一文、昔すぎる時刻、同じ時の書き込み。"""

from __future__ import annotations

import json
import threading
import pytest

from conftest import FAKE_KEY
from omonpakari import journal, quiet_context, storage

H = 3600


def _add(settings, **args):
    return json.loads(journal.make_handler(settings)({"action": "add", **args}))


def _hook(settings, **kwargs):
    return quiet_context.make_hook(settings)(**kwargs)


# ---------------------------------------------------------------------------
# 時の帯
# ---------------------------------------------------------------------------

def test_素の時刻は使う人が据えた帯で読む(hermes, settings):
    hermes.zone.name = "Asia/Tokyo"
    at, tz_min = storage.parse_at("2027-01-15T09:00")
    assert tz_min == 540 and at == hermes.clock.now - 8 * H   # 09:00 (+09:00) は 00:00 UTC
    assert storage.local_tz_min() == 540


def test_帯が据わった機械でも先の時刻と取り違えない(hermes, settings):
    hermes.zone.name = "Asia/Tokyo"
    # 今は 17:00 (+09:00)。地の時刻で 16:30 と言われたら、30分前の記録として刻む
    out = _add(settings, kind="symptom", label="headache", at="2027-01-15T16:30")
    assert out["ok"] is True and out["entry"]["at"] == "2027-01-15T16:30+09:00"
    listed = journal.run({"action": "list", "since": "2027-01-15T16:00"})
    assert listed["matching"] == 1 and listed["since"].endswith("+09:00")


def test_帯の無い時は機械の地の時刻で読む(hermes, settings):
    at, tz_min = storage.parse_at("2027-01-15T09:00")
    assert tz_min == storage.local_tz_min(at)


def test_時差つきの時刻は帯に引きずられない(hermes, settings):
    hermes.zone.name = "Asia/Tokyo"
    assert storage.parse_at("2027-01-15T09:00-05:00") == (hermes.clock.now + 6 * H, -300)


def test_帯の名が誤っていれば機械の地の時刻へ戻る(hermes, settings):
    hermes.zone.name = "Not/AZone"
    at, tz_min = storage.parse_at("2027-01-15T09:00")
    assert tz_min == storage.local_tz_min(at)


def test_夏時間のある帯では日ごとの時差で刻む(hermes, settings):
    hermes.zone.name = "America/New_York"
    assert storage.parse_at("2027-01-15T09:00")[1] == -300
    assert storage.parse_at("2027-07-15T09:00")[1] == -240
    assert storage.local_tz_min() == -300


def test_昔すぎる時刻は刻まない(hermes, settings):
    assert _add(settings, kind="note", label="x", at="0001-01-01T00:00+00:00")["reason"] == "at_too_old"
    assert _add(settings, kind="note", label="x", at="2016-01-01T00:00+00:00")["reason"] == "at_too_old"
    assert _add(settings, kind="note", label="x", at="2026-01-01T00:00+00:00")["ok"] is True


# ---------------------------------------------------------------------------
# 使う人のいない回
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["webhook", "kanban", "batch", "curator", "a2a", "Webhook "])
def test_外から起きた回でも求心路を置かず通信に出ない(hermes, ctx, settings, platform):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    ctx.config.update(latitude=0.0, longitude=0.0)
    _add(settings, kind="symptom", label="headache")
    assert _hook(settings, session_id="s", is_first_turn=True, platform=platform) is None
    assert hermes.spawned == [] and hermes.http.calls == []


@pytest.mark.parametrize("source", ["kanban", "cron", "tool"])
def test_出どころが使う人のいない回なら_platformがcliでも黙る(hermes, ctx, settings, source):
    _add(settings, kind="symptom", label="headache")
    hermes.zone.session_source = source
    assert _hook(settings, session_id="s", is_first_turn=True, platform="cli") is None
    hermes.zone.session_source = ""
    assert _hook(settings, session_id="s", is_first_turn=True, platform="cli") is not None


@pytest.mark.parametrize("platform", ["cli", "tui", "desktop", "telegram", "api_server", ""])
def test_使う人が声をかける場では置く(hermes, settings, platform):
    _add(settings, kind="symptom", label="headache")
    assert _hook(settings, session_id=f"s-{platform}", is_first_turn=True, platform=platform) is not None


# ---------------------------------------------------------------------------
# 求心路の長さ
# ---------------------------------------------------------------------------

def test_上限がいくつあっても終わりの一文を削らない(hermes, settings):
    for i in range(30):
        name = f"medication-name-number-{i:02d}-xxxxxxxxxxxx"
        journal.run({"action": "set_limit", "label": name, "max_count": 5})
        _add(settings, kind="medication", label=name)
    text = _hook(settings, session_id="s", is_first_turn=True, platform="cli")["context"]
    assert len(text) <= quiet_context.MAX_CHARS
    assert text.endswith("\n" + quiet_context.FOOTER + "\n</omonpakari>")
    assert text.startswith("<omonpakari>\n") and text.count("</omonpakari>") == 1
    assert "user-set limits:" in text


# ---------------------------------------------------------------------------
# 同じ時の書き込み
# ---------------------------------------------------------------------------

def test_同じ時に記録と気圧の溜めを書いても失わない(hermes, settings):
    results, errors = [], []

    def writer(i):
        try:
            for _ in range(15):
                results.append(_add(settings, kind="symptom", label=f"w{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def sampler():
        try:
            for j in range(15):
                with storage.connect() as conn:
                    storage.save_samples(conn, [{"kind": "observed", "at_utc": hermes.clock.now - j * 60,
                                                 "hpa": 1010.0}], hermes.clock.now, "p1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)] + [threading.Thread(target=sampler)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and all(r["ok"] for r in results)
    ids = [r["entry"]["id"] for r in results]
    assert len(ids) == len(set(ids)) == 90
    assert journal.run({"action": "list", "limit": 200})["matching"] == 90


def test_背景の取り直しは同じ時に二度走らない(hermes, ctx, settings):
    hermes.secrets["OPENWEATHER_API_KEY"] = FAKE_KEY
    ctx.config.update(latitude=0.0, longitude=0.0)
    assert quiet_context.maybe_refresh(settings) is True
    assert quiet_context.maybe_refresh(settings) is False   # 前の仕事の錠がまだ返っていない
    quiet_context._refresh_lock.release()                  # 前の仕事が終わったことにする
    assert quiet_context.maybe_refresh(settings) is False   # 1時間の間合いの内
    assert len(hermes.spawned) == 1
