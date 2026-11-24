"""Choosing who buys: eligibility (where people are) and fairness (who has done most)."""

from datetime import date

from snack_counter.assignment import VolunteerStats, rank_candidates
from snack_counter.config import BENGALURU, DEFAULT_OFFICES, OSLO
from snack_counter.model import Employee, Registry

from conftest import inr, nok

MONDAY = date(2026, 11, 23)
LIMITS = {o.currency: o.outstanding_limit for o in DEFAULT_OFFICES.values()}


def registry_with(*employees: Employee) -> Registry:
    reg = Registry(DEFAULT_OFFICES)
    for e in employees:
        reg.add(e)
    return reg


def ids(emps):
    return [e.employee_id for e in emps]


def rank(reg, office=OSLO, day=MONDAY, stats=None, exclude=()):
    stats = stats or {}
    return ids(
        rank_candidates(office, day, reg, lambda e: stats.get(e, VolunteerStats()), set(exclude), LIMITS)
    )


def test_only_volunteers_physically_in_the_office_are_eligible():
    reg = registry_with(
        Employee("in_office", "A", "OSL", "NOK"),
        Employee("remote_today", "B", "OSL", "NOK", office_weekdays=frozenset({1, 3})),  # Tue/Thu only
        Employee("on_holiday", "C", "OSL", "NOK"),
        Employee("not_volunteer", "D", "OSL", "NOK", is_volunteer=False),
    )
    reg.set_away("on_holiday", date(2026, 11, 20), date(2026, 11, 27))
    assert rank(reg) == ["in_office"]


def test_visitor_refills_the_office_they_are_in_not_their_home_office():
    reg = registry_with(Employee("priya", "Priya", "BLR", "INR"), Employee("arjun", "Arjun", "BLR", "INR"))
    reg.set_visiting("priya", "OSL", date(2026, 11, 23), date(2026, 12, 4))
    assert rank(reg, office=OSLO) == ["priya"]
    assert rank(reg, office=BENGALURU) == ["arjun"]
    # After the visit she is back in the Bengaluru rotation
    assert rank(reg, office=BENGALURU, day=date(2026, 12, 7)) == ["arjun", "priya"]


def test_nobody_is_eligible_on_weekends_or_office_holidays():
    reg = registry_with(Employee("kari", "Kari", "OSL", "NOK"))
    assert rank(reg, day=date(2026, 11, 28)) == []  # Saturday
    assert rank(reg, day=date(2026, 12, 24)) == []  # Oslo holiday


def test_fewest_recent_refills_goes_first():
    reg = registry_with(*(Employee(e, e, "OSL", "NOK") for e in ("a", "b", "c")))
    stats = {"a": VolunteerStats(3), "b": VolunteerStats(1), "c": VolunteerStats(2)}
    assert rank(reg, stats=stats) == ["b", "c", "a"]


def test_tie_broken_by_less_money_owed_then_longest_since_last_refill():
    reg = registry_with(*(Employee(e, e, "OSL", "NOK") for e in ("a", "b", "c")))
    stats = {
        "a": VolunteerStats(1, {"NOK": nok(800)}, date(2026, 11, 1)),
        "b": VolunteerStats(1, {}, date(2026, 11, 20)),
        "c": VolunteerStats(1, {}, date(2026, 11, 5)),
    }
    assert rank(reg, stats=stats) == ["c", "b", "a"]


def test_volunteer_owed_more_than_the_limit_is_skipped():
    reg = registry_with(Employee("at_limit", "A", "OSL", "NOK"), Employee("over", "B", "OSL", "NOK"))
    stats = {
        "at_limit": VolunteerStats(0, {"NOK": nok(1500)}),
        "over": VolunteerStats(0, {"NOK": nok("1500.01")}),
    }
    assert rank(reg, stats=stats) == ["at_limit"]


def test_limit_applies_per_currency_for_visitors():
    reg = registry_with(Employee("priya", "Priya", "BLR", "INR"))
    reg.set_visiting("priya", "OSL", date(2026, 11, 23), date(2026, 12, 4))
    assert rank(reg, stats={"priya": VolunteerStats(0, {"INR": inr(3999)})}) == ["priya"]
    assert rank(reg, stats={"priya": VolunteerStats(0, {"INR": inr(4001)})}) == []


def test_already_tried_people_are_excluded():
    reg = registry_with(Employee("a", "A", "OSL", "NOK"), Employee("b", "B", "OSL", "NOK"))
    assert rank(reg, exclude={"a"}) == ["b"]
