"""One monthly report across both offices."""

from decimal import Decimal

import pytest

from snack_counter.reporting import monthly_report

from conftest import inr, nok, oslo_time


def test_monthly_report_native_totals_and_combined_nok(world):
    world.complete_refill("OSL", nok(600), tags=["chips", "chocolate"])
    world.clock.set(oslo_time(2026, 11, 26, 9))
    world.complete_refill("OSL", nok(400), tags=["chips"])
    world.complete_refill("BLR", inr(1500), tags=["samosa"])
    world.complete_refill("BLR", inr(1000), tags=["biscuits"])

    report = monthly_report(world.service, world.ledger, "2026-11", {"INR": Decimal("0.125")})
    osl, blr = report.offices
    assert osl.spent == nok(1000) and osl.remaining == nok(5000)
    assert blr.spent == inr(2500) and blr.remaining == inr(12500)
    assert report.combined_total == nok("1312.50")  # 1 000 + 2 500 * 0.125
    assert osl.top_tags[0] == ("chips", 2)
    assert osl.avg_days_between_refills == 3.0
    assert report.fx_rates == {"INR": Decimal("0.125")}
    assert "Combined total: 1,312.50 NOK" in report.render()


def test_report_excludes_other_months_and_cancelled_refills(world):
    world.complete_refill("OSL", nok(500))
    world.clock.set(oslo_time(2026, 12, 1, 9))
    world.complete_refill("OSL", nok(700))

    report = monthly_report(world.service, world.ledger, "2026-11", {"INR": Decimal("0.125")})
    assert report.offices[0].spent == nok(500)
    assert report.offices[0].refill_count == 1


def test_report_requires_an_explicit_fx_rate(world):
    with pytest.raises(KeyError):
        monthly_report(world.service, world.ledger, "2026-11", {})


def test_report_shows_who_carries_the_load_without_penalising_anyone(world):
    # Refill 1: kari declines, nils lets it time out, ola accepts and buys.
    r1 = world.service.report_empty("OSL", "erik")
    world.service.decline(r1.refill_id, "kari")
    world.clock.set(oslo_time(2026, 11, 23, 13, 0))
    world.service.tick()  # nils: no answer
    world.service.accept(r1.refill_id, "ola", r1.version)
    world.service.record_purchase(r1.refill_id, "ola", nok(800), "r1.jpg")
    # Refill 2 (next day): kari accepts but misses the deadline; it passes on to nils.
    world.clock.set(oslo_time(2026, 11, 24, 9, 0))
    r2 = world.service.report_empty("OSL", "erik")
    assert r2.assignee == "kari"
    world.service.accept(r2.refill_id, "kari", r2.version)
    world.clock.set(oslo_time(2026, 11, 25, 17, 0))
    world.service.tick()

    report = monthly_report(world.service, world.ledger, "2026-11", {"INR": Decimal("0.125")})
    activity = {a.employee_id: a for a in report.offices[0].volunteer_activity}
    assert (activity["kari"].offered, activity["kari"].declined, activity["kari"].accepted, activity["kari"].not_delivered) == (2, 1, 1, 1)
    assert (activity["nils"].offered, activity["nils"].no_answer) == (2, 1)
    assert (activity["ola"].offered, activity["ola"].accepted) == (1, 1)
    assert "kari: offered 2, accepted 1, declined 1, no answer 0, not delivered 1" in report.render()
