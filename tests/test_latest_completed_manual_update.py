import datetime as dt
from pathlib import Path

import server
from qtrade_adapters.deepseek_harness import runtime


CALENDAR = [
    dt.date(2025, 12, 31),
    dt.date(2026, 1, 5),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]


def test_latest_completed_trade_date_boundaries_holiday_and_weekend():
    resolve = runtime.resolve_latest_completed_trade_date
    assert resolve(dt.datetime(2026, 8, 28, 18, 29, 59), CALENDAR) == dt.date(2026, 8, 27)
    assert resolve(dt.datetime(2026, 8, 28, 18, 30, 0), CALENDAR) == dt.date(2026, 8, 28)
    assert resolve(dt.datetime(2026, 8, 29, 12), CALENDAR) == dt.date(2026, 8, 28)
    assert resolve(dt.datetime(2026, 8, 30, 12), CALENDAR) == dt.date(2026, 8, 28)
    assert resolve(dt.datetime(2026, 8, 31, 18, 29), CALENDAR) == dt.date(2026, 8, 28)


def test_latest_completed_trade_date_cross_year_and_timezone_equivalence():
    resolve = runtime.resolve_latest_completed_trade_date
    local = dt.datetime(2026, 1, 5, 18, 30, tzinfo=runtime.SHANGHAI_TZ)
    utc = local.astimezone(dt.timezone.utc)
    assert resolve(local, CALENDAR) == dt.date(2026, 1, 5)
    assert resolve(utc, CALENDAR) == dt.date(2026, 1, 5)
    assert resolve(dt.datetime(2026, 1, 1, 12), CALENDAR) == dt.date(2025, 12, 31)


def test_latest_completed_trade_date_fails_closed_for_bad_or_stale_calendar():
    for calendar in ([], ["bad"], [dt.date(2026, 8, 27)]):
        try:
            runtime.resolve_latest_completed_trade_date(dt.datetime(2026, 8, 28, 19), calendar)
        except ValueError as error:
            assert str(error) == "calendar_unavailable"
        else:
            raise AssertionError("calendar must fail closed")


def test_manual_calendar_resolution_runs_in_worker_and_freezes_target(tmp_path):
    calls = []
    clock_values = iter([
        dt.datetime(2026, 8, 29, 9),
        dt.datetime(2026, 8, 30, 20),
        dt.datetime(2026, 8, 31, 20),
    ])

    def clock():
        return next(clock_values, dt.datetime(2026, 8, 31, 20))

    def run(base, target, **kwargs):
        calls.append(target)
        return 1

    controller = runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path,
        project_root=tmp_path,
        status_file=tmp_path / "status.json",
        lock_path=tmp_path / "manual.lock",
        pipeline_lock_path=tmp_path / "pipeline.lock",
        clock=clock,
        run_fn=run,
        calendar_loader=lambda: CALENDAR,
    )
    accepted = controller.start(now=dt.datetime(2026, 8, 29, 9))
    assert accepted["state"] == "accepted"
    assert accepted["trade_date"] is None
    controller._worker.join(2)
    assert calls == [dt.date(2026, 8, 28)]


def test_manual_payload_preserves_large_stock_counts_and_separate_pipeline_progress():
    payload = server._safe_manual_update_payload({
        "state": "running",
        "trade_date": "2026-08-28",
        "progress": {"completed": 1200, "total": 3617, "current": "portal"},
        "pipeline_progress": {"completed": 0, "total": 4, "current": "portal"},
        "stock_progress": {"completed": 1200, "total": 3617, "failed": 7, "pending": 2410},
    })
    assert payload["progress"]["completed"] == 1200
    assert payload["progress"]["total"] == 3617
    assert payload["pipeline_progress"] == {"completed": 0, "total": 4, "current": "portal"}
    assert payload["stock_progress"] == {"completed": 1200, "total": 3617, "failed": 7, "pending": 2410}
