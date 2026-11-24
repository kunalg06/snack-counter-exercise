"""Budgets and totals are correct across both currencies."""

from datetime import date

import pytest

from snack_counter.model import RefillState as S
from snack_counter.refills import InvalidTransition, NotAuthorized

from conftest import inr, nok, oslo_time


def test_cap_shrinks_to_the_remaining_budget_and_stops_below_the_minimum(world):
    for amount in (1200, 1200, 1200, 1200, 600):  # 5 400 NOK
        world.complete_refill("OSL", nok(amount))
    assert world.ledger.remaining("OSL", "2026-11") == nok(600)

    r = world.service.report_empty("OSL", "erik")
    assert r.cap == nok(600)
    assert "600.00 NOK" in world.chat.dms_to(r.assignee)[-1]
    world.service.accept(r.refill_id, r.assignee, r.version)
    world.service.record_purchase(r.refill_id, r.assignee, nok(400), "r.jpg")
    assert world.ledger.remaining("OSL", "2026-11") == nok(200)

    blocked = world.service.report_empty("OSL", "erik")
    assert blocked.state == S.ESCALATED and blocked.escalation_reason == "budget"
    assert blocked.offers == []  # nobody was asked to spend money we don't have
    assert "only 200.00 NOK left" in world.chat.dms_to("oslo-coord")[-1]
    with pytest.raises(InvalidTransition):
        world.service.accept(blocked.refill_id, "kari", blocked.version)


def test_budget_hold_lifts_automatically_when_the_new_month_starts(world):
    world.clock.set(oslo_time(2026, 11, 30, 9))
    for _ in range(5):
        world.complete_refill("OSL", nok(1200))
    blocked = world.service.report_empty("OSL", "erik")
    assert blocked.escalation_reason == "budget"

    world.clock.set(oslo_time(2026, 12, 1, 9))
    world.service.tick()
    assert blocked.state == S.OFFERED
    assert blocked.cap == nok(1200)


def test_oslo_and_bengaluru_budgets_never_affect_each_other(world):
    world.complete_refill("OSL", nok(1200))
    world.complete_refill("BLR", inr(2500))
    assert world.ledger.remaining("OSL", "2026-11") == nok(4800)
    assert world.ledger.remaining("BLR", "2026-11") == inr(12500)


def test_bengaluru_cap_uses_inr_amounts(world):
    r = world.service.report_empty("BLR", "ravi")
    assert r.cap == inr(3000)


def test_visitor_buying_in_oslo_spends_oslo_budget_in_nok(world):
    world.registry.set_visiting("priya", "OSL", date(2026, 11, 23), date(2026, 12, 4))
    for who in ("kari", "nils", "ola"):
        world.registry.set_away(who, date(2026, 11, 23), date(2026, 11, 23))

    r = world.service.report_empty("OSL", "erik")
    assert r.assignee == "priya"
    world.service.accept(r.refill_id, "priya", r.version)
    world.service.record_purchase(r.refill_id, "priya", nok(500), "oslo-receipt.jpg")

    assert world.ledger.spent("OSL", "2026-11") == nok(500)
    assert world.ledger.spent("BLR", "2026-11") == inr(0)
    claim = world.hr.claims[r.hr_claim_id]
    assert claim.employee_id == "priya" and claim.amount == nok(500)  # HR converts to her INR payroll

    # While visiting, she isn't offered Bengaluru refills
    blr = world.service.report_empty("BLR", "ravi")
    assert "priya" not in blr.tried


def test_spend_counts_in_the_month_on_the_receipt(world):
    world.clock.set(oslo_time(2026, 11, 30, 9))
    nov = world.complete_refill("OSL", nok(700))
    assert world.ledger.spent("OSL", "2026-11") == nok(700)

    # Accepted on 30 Nov, but bought (receipt date) on 1 Dec: counts in December
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, r.assignee, r.version)
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)
    world.clock.set(oslo_time(2026, 12, 1, 9))
    world.service.record_purchase(r.refill_id, r.assignee, nok(900), "r.jpg")
    assert world.ledger.spent("OSL", "2026-11") == nok(700)
    assert world.ledger.reserved("OSL", "2026-11") == nok(0)
    assert world.ledger.spent("OSL", "2026-12") == nok(900)
    assert world.ledger.remaining("OSL", "2026-12") == nok(5100)
    assert nov.purchase_date == date(2026, 11, 30)


def test_receipt_above_cap_waits_for_coordinator_approval(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.service.record_purchase(r.refill_id, "kari", nok(1300), "big.jpg")

    assert r.state == S.AWAITING_APPROVAL
    assert world.hr.claims == {}  # not sent to HR yet
    assert world.ledger.spent("OSL", "2026-11") == nok(1300)  # the money was really spent
    assert "above the cap" in world.chat.dms_to("oslo-coord")[-1]

    with pytest.raises(NotAuthorized):
        world.service.approve_over_cap(r.refill_id, "nils")
    world.service.approve_over_cap(r.refill_id, "oslo-coord")
    assert r.state == S.CLAIM_SUBMITTED
    assert len(world.hr.claims) == 1


def test_accept_rechecks_the_budget_if_it_shrank_since_the_offer(world):
    for _ in range(4):
        world.complete_refill("OSL", nok(1200))  # 4 800 spent
    e = world.service.report_empty("OSL", "erik")
    world.service.accept(e.refill_id, e.assignee, e.version)
    world.service.record_purchase(e.refill_id, e.assignee, nok(900), "e.jpg")  # 5 700 spent
    world.hr.reject(e.hr_claim_id, "Blurry")
    world.service.sync_hr_status()

    f = world.service.report_empty("OSL", "erik")
    assert f.state == S.OFFERED and f.cap == nok(300)  # 300 left when offered
    world.service.resubmit(e.refill_id, e.assignee, "e-clear.jpg", amount=nok(1000))  # now 200 left

    with pytest.raises(InvalidTransition):
        world.service.accept(f.refill_id, f.assignee, f.version)
    assert f.state == S.ESCALATED and f.escalation_reason == "budget"
    assert f.offers[-1].outcome == "budget_hold"
    assert world.ledger.reserved("OSL", "2026-11") == nok(0)
    assert "only 200.00 NOK left" in world.chat.dms_to("oslo-coord")[-1]


def test_offer_made_at_month_end_and_accepted_next_month_uses_the_new_budget(world):
    world.clock.set(oslo_time(2026, 11, 30, 9))
    for amount in (1200, 1200, 1200, 1200, 700):  # 5 500 spent in November
        world.complete_refill("OSL", nok(amount))
    world.clock.set(oslo_time(2026, 11, 30, 16))
    r = world.service.report_empty("OSL", "erik")
    assert r.cap == nok(500)

    world.clock.set(oslo_time(2026, 12, 1, 9, 30))
    world.service.accept(r.refill_id, r.assignee, r.version)
    assert r.cap == nok(1200)
    assert world.ledger.reserved("OSL", "2026-12") == nok(1200)
    assert world.ledger.reserved("OSL", "2026-11") == nok(0)


def test_purchase_date_cannot_be_in_the_future_or_before_the_report(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    with pytest.raises(ValueError, match="future"):
        world.service.record_purchase(r.refill_id, "kari", nok(500), "r.jpg", purchase_date=date(2026, 11, 24))
    with pytest.raises(ValueError, match="before"):
        world.service.record_purchase(r.refill_id, "kari", nok(500), "r.jpg", purchase_date=date(2026, 11, 22))
    assert r.state == S.CLAIMED
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)  # nothing was settled


def test_offices_sharing_a_currency_must_share_the_outstanding_limit(world):
    from dataclasses import replace

    from snack_counter.config import OSLO
    from snack_counter.refills import RefillService

    bergen = replace(OSLO, code="BRG", name="Bergen", outstanding_limit=nok(2000))
    with pytest.raises(ValueError, match="same outstanding limit"):
        RefillService([OSLO, bergen], world.registry, world.ledger, world.chat, world.hr, world.clock)


def test_budget_hold_rotation_restarts_fresh_next_month_and_volunteer_is_told(world):
    for _ in range(4):
        world.complete_refill("OSL", nok(1200))
    e = world.service.report_empty("OSL", "erik")
    world.service.accept(e.refill_id, e.assignee, e.version)
    world.service.record_purchase(e.refill_id, e.assignee, nok(900), "e.jpg")
    world.hr.reject(e.hr_claim_id, "Blurry")
    world.service.sync_hr_status()
    f = world.service.report_empty("OSL", "erik")
    first_offeree = f.assignee
    world.service.resubmit(e.refill_id, e.assignee, "e-clear.jpg", amount=nok(1000))
    with pytest.raises(InvalidTransition):
        world.service.accept(f.refill_id, first_offeree, f.version)
    assert "budget has run out" in world.chat.dms_to(first_offeree)[-1]  # the clicker is told

    world.clock.set(oslo_time(2026, 12, 1, 9))
    world.service.tick()
    assert f.state == S.OFFERED
    assert f.assignee == first_offeree  # new round: the earlier offeree is not skipped


def test_over_cap_receipt_left_unapproved_reminds_the_coordinators(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.service.record_purchase(r.refill_id, "kari", nok(1300), "big.jpg")  # Monday 09:00
    world.clock.set(oslo_time(2026, 11, 24, 16, 59))
    world.service.tick()
    assert not any(m.startswith("Reminder") for m in world.chat.dms_to("oslo-coord"))
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()
    reminders = [m for m in world.chat.dms_to("oslo-backup") if m.startswith("Reminder")]
    assert len(reminders) == 1 and "waiting for your approval for 2 working days" in reminders[0]
