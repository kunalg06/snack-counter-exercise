from datetime import date

from snack_counter.config import BENGALURU, OSLO
from snack_counter.working_time import add_working_hours, end_of_next_working_day

from conftest import blr_time, oslo_time


def test_within_the_same_day():
    assert add_working_hours(oslo_time(2026, 11, 23, 9), 2, OSLO) == oslo_time(2026, 11, 23, 11)


def test_friday_afternoon_offer_does_not_expire_over_the_weekend():
    # Friday 16:30 + 2 working hours = Monday 10:30
    assert add_working_hours(oslo_time(2026, 11, 27, 16, 30), 2, OSLO) == oslo_time(2026, 11, 30, 10, 30)


def test_outside_hours_starts_counting_at_nine():
    assert add_working_hours(oslo_time(2026, 11, 23, 7, 0), 2, OSLO) == oslo_time(2026, 11, 23, 11)
    assert add_working_hours(oslo_time(2026, 11, 23, 20, 0), 2, OSLO) == oslo_time(2026, 11, 24, 11)


def test_public_holidays_are_skipped():
    # Wed 23 Dec 16:00: 1h left that day; 24th and 25th are holidays, then the weekend -> Mon 28 Dec 10:00
    assert add_working_hours(oslo_time(2026, 12, 23, 16), 2, OSLO) == oslo_time(2026, 12, 28, 10)


def test_each_office_uses_its_own_time_zone():
    # 09:00 in Oslo is 13:30 in Bengaluru: Bengaluru has 3.5h left that day
    start = oslo_time(2026, 11, 23, 9)
    assert add_working_hours(start, 2, BENGALURU) == blr_time(2026, 11, 23, 15, 30)
    assert add_working_hours(start, 4, BENGALURU) == blr_time(2026, 11, 24, 9, 30)


def test_buy_deadline_is_end_of_next_working_day():
    assert end_of_next_working_day(oslo_time(2026, 11, 23, 10), OSLO) == oslo_time(2026, 11, 24, 17)
    assert end_of_next_working_day(oslo_time(2026, 11, 27, 10), OSLO) == oslo_time(2026, 11, 30, 17)


def test_easter_is_computed_correctly():
    from snack_counter.holidays import easter_sunday

    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2024) == date(2024, 3, 31)


def test_norway_moving_holidays_are_derived_from_easter():
    from snack_counter.holidays import norway_holidays

    h = norway_holidays(2026)
    for d in (date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6),  # Maundy Thu, Good Fri, Easter Mon
              date(2026, 5, 14), date(2026, 5, 25),                  # Ascension, Whit Monday
              date(2026, 5, 1), date(2026, 5, 17)):
        assert d in h


def test_easter_week_in_oslo_pauses_deadlines():
    # Wed 1 Apr 2026 16:00 + 2h: Thu/Fri are Easter holidays, then the weekend, then Easter Monday
    assert add_working_hours(oslo_time(2026, 4, 1, 16), 2, OSLO) == oslo_time(2026, 4, 7, 10)


def test_bengaluru_festival_pauses_deadlines():
    # Thu 26 Nov 2026 16:00 IST + 2h: Fri 27 Nov is Kanakadasa Jayanthi -> Mon 30 Nov 10:00
    assert add_working_hours(blr_time(2026, 11, 26, 16), 2, BENGALURU) == blr_time(2026, 11, 30, 10)
    assert date(2027, 10, 28) in BENGALURU.holidays and date(2027, 10, 30) in BENGALURU.holidays  # Deepavali 2027


def test_both_offices_have_calendars_for_2026_and_2027():
    for office in (OSLO, BENGALURU):
        for year in (2026, 2027):
            assert sum(d.year == year for d in office.holidays) >= 10, (office.code, year)
