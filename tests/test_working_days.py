from datetime import date

from app.tools.working_days import applicable_calendar, count_working_days, public_holidays

HOL = {date(2026, 11, 9): "Deepavali", date(2026, 12, 25): "Christmas Day"}


def test_weekend_only_range_has_zero_working_days():
    assert count_working_days(date(2026, 11, 21), date(2026, 11, 22), {}) == (0, [])


def test_single_weekday():
    assert count_working_days(date(2026, 11, 18), date(2026, 11, 18), {})[0] == 1


def test_full_week_with_holiday_excluded():
    n, skipped = count_working_days(date(2026, 11, 9), date(2026, 11, 13), HOL)
    assert n == 4
    assert skipped == [(date(2026, 11, 9), "Deepavali")]


def test_range_spanning_two_weekends_and_a_holiday():
    # Fri 2026-12-18 .. Mon 2026-12-28: weekdays 18,21,22,23,24,25,28 -> 7, minus Christmas -> 6
    n, _ = count_working_days(date(2026, 12, 18), date(2026, 12, 28), HOL)
    assert n == 6


def test_holiday_on_weekend_is_not_double_counted():
    hol = {date(2026, 11, 1): "Sunday holiday"}
    assert count_working_days(date(2026, 10, 30), date(2026, 11, 2), hol)[0] == 2


def test_end_before_start_is_zero():
    assert count_working_days(date(2026, 11, 20), date(2026, 11, 18), {})[0] == 0


def test_calendar_is_region_specific_and_restricted_holidays_are_not_closures(base_env):
    from app.ingestion.pipeline import KB
    today = date(2026, 11, 17)
    in_hol, in_cal = public_holidays(KB.docs, "employee", "IN", 2026, today)
    de_hol, de_cal = public_holidays(KB.docs, "employee", "DE", 2026, today)
    assert in_cal.doc_id == "CAL-IN-2026" and de_cal.doc_id == "CAL-DE-2026"
    assert date(2026, 11, 9) in in_hol and date(2026, 11, 9) not in de_hol
    assert date(2026, 12, 24) in de_hol and date(2026, 12, 24) not in in_hol
    assert date(2026, 11, 24) not in in_hol          # restricted (optional) holiday: office open
    assert applicable_calendar(KB.docs, "employee", "US", 2026, today) is None
