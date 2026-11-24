"""Two people can't both commit, and a refill nobody completes is handed to someone else."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from snack_counter.budget import BudgetLedger
from snack_counter.model import RefillState as S
from snack_counter.refills import (
    AlreadyClaimed,
    InvalidTransition,
    NotAuthorized,
    NotOfferedToYou,
    StaleVersion,
)

from conftest import nok, oslo_time


class BlockingLedger(BudgetLedger):
    """The first reserve() pauses inside accept() until the test lets it continue."""

    def __init__(self, offices):
        super().__init__(offices)
        self.entered = threading.Event()
        self.gate = threading.Event()
        self._first = True

    def reserve(self, *args, **kwargs):
        if self._first:
            self._first = False
            self.entered.set()
            assert self.gate.wait(5)
        super().reserve(*args, **kwargs)


def escalate_oslo(world):
    """Everyone in Oslo declines -> the refill escalates to a broadcast."""
    r = world.service.report_empty("OSL", "erik")
    for who in ("kari", "nils", "ola"):
        world.service.decline(r.refill_id, who)
    assert r.state == S.ESCALATED
    return r


# --- two people cannot both commit ------------------------------------------
def test_only_the_offered_volunteer_can_accept(world):
    r = world.service.report_empty("OSL", "erik")
    with pytest.raises(NotOfferedToYou):
        world.service.accept(r.refill_id, "nils", r.version)
    assert r.state == S.OFFERED and r.assignee == "kari"


def test_second_accept_of_the_same_refill_fails(world):
    r = world.service.report_empty("OSL", "erik")
    version = r.version
    world.service.accept(r.refill_id, "kari", version)
    with pytest.raises(AlreadyClaimed):
        world.service.accept(r.refill_id, "kari", version)
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)  # reserved once, not twice


def test_two_volunteers_racing_on_a_broadcast_exactly_one_wins(world):
    r = escalate_oslo(world)
    version = r.version
    world.service.accept(r.refill_id, "nils", version)
    with pytest.raises(AlreadyClaimed):
        world.service.accept(r.refill_id, "ola", version)
    assert r.assignee == "nils"


def test_concurrent_accepts_on_threads_exactly_one_wins(world):
    r = escalate_oslo(world)
    version = r.version
    barrier = threading.Barrier(3)

    def try_accept(who):
        barrier.wait()
        try:
            world.service.accept(r.refill_id, who, version)
            return "won"
        except AlreadyClaimed:
            return "lost"

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(try_accept, ["kari", "nils", "ola"]))
    assert sorted(results) == ["lost", "lost", "won"]
    assert r.state == S.CLAIMED
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)


def test_second_accept_is_blocked_while_the_first_is_mid_commit(world):
    """Deterministic race: kari is paused inside accept() (after every check, before the
    reservation). nils clicks at that moment. Without the lock both would pass the checks."""
    r = escalate_oslo(world)
    ledger = BlockingLedger(world.offices)
    world.service.ledger = ledger
    version = r.version
    results = {}

    def run(who):
        try:
            world.service.accept(r.refill_id, who, version)
            results[who] = "won"
        except Exception as e:  # record whatever happens, assert below
            results[who] = type(e).__name__

    kari = threading.Thread(target=run, args=("kari",))
    kari.start()
    assert ledger.entered.wait(5)  # kari is mid-commit, holding the lock
    nils = threading.Thread(target=run, args=("nils",))
    nils.start()
    nils.join(timeout=0.2)
    assert nils.is_alive()  # nils waits for kari's commit to finish
    ledger.gate.set()
    kari.join(5)
    nils.join(5)

    assert results == {"kari": "won", "nils": "AlreadyClaimed"}
    assert ledger.reserved("OSL", "2026-11") == nok(1200)
    assert not any("is yours" in m for m in world.chat.dms_to("nils"))


def test_stale_button_click_is_rejected(world):
    r = escalate_oslo(world)
    with pytest.raises(StaleVersion):
        world.service.accept(r.refill_id, "nils", r.version - 1)


def test_broadcast_accept_requires_a_volunteer_in_that_office_today(world):
    r = escalate_oslo(world)
    with pytest.raises(NotAuthorized):
        world.service.accept(r.refill_id, "erik", r.version)  # not a volunteer
    with pytest.raises(NotAuthorized):
        world.service.accept(r.refill_id, "arjun", r.version)  # in Bengaluru
    with pytest.raises(NotAuthorized):
        world.service.accept(r.refill_id, "ghost", r.version)  # unknown employee


def test_broadcast_accept_respects_the_owed_money_limit(world):
    first = world.service.report_empty("OSL", "erik")
    world.service.accept(first.refill_id, "kari", first.version)
    world.service.record_purchase(first.refill_id, "kari", nok(1600), "big.jpg")  # over cap, still owed
    assert world.service.stats_for("kari").outstanding["NOK"] == nok(1600)

    r = world.service.report_empty("OSL", "erik")
    for who in ("nils", "ola"):  # kari is skipped by the rotation (over the 1 500 limit)
        world.service.decline(r.refill_id, who)
    assert r.state == S.ESCALATED
    with pytest.raises(NotAuthorized):
        world.service.accept(r.refill_id, "kari", r.version)
    world.service.accept(r.refill_id, "nils", r.version)
    assert r.assignee == "nils"


# --- a refill nobody completes is handed on ----------------------------------
def test_decline_passes_the_refill_to_the_next_volunteer(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.decline(r.refill_id, "kari")
    assert r.state == S.OFFERED and r.assignee == "nils"
    assert [o.outcome for o in r.offers] == ["declined", None]


def test_unanswered_offer_times_out_and_moves_on(world):
    r = world.service.report_empty("OSL", "erik")
    kari_version = r.version
    assert r.offer_deadline == oslo_time(2026, 11, 23, 11, 0)

    world.clock.set(oslo_time(2026, 11, 23, 10, 59))
    world.service.tick()
    assert r.assignee == "kari"  # not yet

    world.clock.set(oslo_time(2026, 11, 23, 11, 0))
    world.service.tick()
    assert r.assignee == "nils"
    assert r.offers[0].outcome == "expired"
    assert "passed on" in world.chat.dms_to("kari")[-1]  # the old offeree is told...
    assert "Can you refill it" in world.chat.dms_to("nils")[-1]  # ...the new one gets the offer
    with pytest.raises(NotOfferedToYou):
        world.service.accept(r.refill_id, "kari", kari_version)


def test_expired_offer_cannot_be_accepted_even_before_the_scheduler_runs(world):
    r = world.service.report_empty("OSL", "erik")
    world.clock.set(oslo_time(2026, 11, 23, 11, 0))  # deadline reached, tick() not run yet
    with pytest.raises(NotOfferedToYou, match="expired"):
        world.service.accept(r.refill_id, "kari", r.version)
    assert r.state == S.OFFERED and world.ledger.reserved("OSL", "2026-11") == nok(0)


def test_friday_afternoon_offer_waits_until_monday(world):
    world.clock.set(oslo_time(2026, 11, 27, 16, 30))
    r = world.service.report_empty("OSL", "erik")
    assert r.offer_deadline == oslo_time(2026, 11, 30, 10, 30)
    world.clock.set(oslo_time(2026, 11, 28, 12, 0))  # Saturday
    world.service.tick()
    assert r.assignee == "kari"
    world.clock.set(oslo_time(2026, 11, 30, 10, 30))
    world.service.tick()
    assert r.assignee == "nils"


def test_claimed_refill_not_bought_in_time_is_released_and_reoffered(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    assert r.buy_deadline == oslo_time(2026, 11, 24, 17, 0)
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)

    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()
    assert r.state == S.OFFERED and r.assignee == "nils"
    assert world.ledger.reserved("OSL", "2026-11") == nok(0)  # reservation freed
    assert "passed on" in world.chat.dms_to("kari")[-1]
    with pytest.raises(NotOfferedToYou):  # someone never assigned can't book a receipt on it
        world.service.record_purchase(r.refill_id, "ola", nok(500), "x.jpg")


def test_late_buyer_is_not_stranded_their_receipt_goes_to_the_coordinator(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()  # released and passed on to nils

    late = world.service.record_purchase(r.refill_id, "kari", nok(500), "late.jpg")
    assert late is not r and late.late_receipt_of == r.refill_id
    assert late.state == S.AWAITING_APPROVAL
    assert world.hr.claims == {}  # not sent until a coordinator approves
    assert world.ledger.spent("OSL", "2026-11") == nok(500)  # the money was really spent
    coordinator_msg = world.chat.dms_to("oslo-coord")[-1]
    assert "bought late" in coordinator_msg and "Budget left this month: 5,500.00 NOK" in coordinator_msg
    assert world.chat.dms_to("oslo-backup")[-1] == coordinator_msg  # backup coordinator sees it too
    assert "coordinator will review" in world.chat.dms_to("kari")[-1]
    # the original was only offered to nils (not claimed): closed, and nils told not to buy
    assert r.state == S.CANCELLED and "late receipt" in r.cancel_reason
    assert "No need to refill" in world.chat.dms_to("nils")[-1]
    with pytest.raises(InvalidTransition):  # only one late receipt per person and refill
        world.service.record_purchase(r.refill_id, "kari", nok(500), "late.jpg")

    world.service.approve_over_cap(late.refill_id, "oslo-coord")
    assert late.state == S.CLAIM_SUBMITTED
    assert world.hr.claims[late.hr_claim_id].employee_id == "kari"


def test_volunteer_is_reminded_two_hours_before_the_buy_deadline(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)  # due Tue 17:00

    def reminders():
        return [m for m in world.chat.dms_to("kari") if m.startswith("Reminder")]

    world.clock.set(oslo_time(2026, 11, 24, 14, 59))
    world.service.tick()
    assert reminders() == []
    world.clock.set(oslo_time(2026, 11, 24, 15, 0))
    world.service.tick()
    world.service.tick()
    assert len(reminders()) == 1
    assert r.state == S.CLAIMED


def test_stalled_broadcast_restarts_the_rotation_next_working_day(world):
    r = escalate_oslo(world)  # Monday: everyone declined
    world.clock.set(oslo_time(2026, 11, 23, 16, 0))
    world.service.tick()
    assert r.state == S.ESCALATED  # same day: leave the broadcast up
    world.clock.set(oslo_time(2026, 11, 24, 8, 59))
    world.service.tick()
    assert r.state == S.ESCALATED  # before office hours
    world.clock.set(oslo_time(2026, 11, 24, 9, 0))
    world.service.tick()
    assert r.state == S.OFFERED and r.assignee == "kari"  # fresh round, people may be back
    assert len(r.offers) == 4


def test_when_everyone_declines_it_escalates_to_coordinator_and_channel(world):
    r = escalate_oslo(world)
    assert r.escalation_reason == "no_candidates"
    assert "needs attention" in world.chat.dms_to("oslo-coord")[-1]
    assert len(world.chat.channel_posts("OSL")) == 1
    # Someone who declined earlier can still pick it up from the broadcast
    world.service.accept(r.refill_id, "kari", r.version)
    assert r.state == S.CLAIMED


def test_people_on_holiday_or_remote_are_never_offered(world):
    from datetime import date

    world.registry.get("kari").office_weekdays = frozenset({1, 3})  # remote on Mondays
    world.registry.set_away("nils", date(2026, 11, 23), date(2026, 11, 27))
    r = world.service.report_empty("OSL", "erik")
    assert r.assignee == "ola"
    world.service.decline(r.refill_id, "ola")
    assert r.state == S.ESCALATED  # kari and nils were never asked
    assert world.chat.dms_to("kari") == [] and world.chat.dms_to("nils") == []


def test_fairness_the_person_who_refilled_last_is_not_asked_next(world):
    first = world.complete_refill("OSL", nok(800))
    assert first.assignee == "kari"
    second = world.complete_refill("OSL", nok(800))
    assert second.assignee == "nils"
    third = world.service.report_empty("OSL", "erik")
    assert third.assignee == "ola"


def test_volunteer_still_waiting_for_money_is_skipped(world):
    # kari is owed 1 200 NOK from the first refill (not yet reimbursed)
    world.complete_refill("OSL", nok(1200), reimburse=False)
    world.complete_refill("OSL", nok(1200), reimburse=False)  # nils, now also owed 1 200
    world.complete_refill("OSL", nok(1200), reimburse=False)  # ola
    # Everyone has done one refill and is owed 1 200 (under the 1 500 limit): rotation restarts at kari
    r = world.service.report_empty("OSL", "erik")
    assert r.assignee == "kari"
    world.service.accept(r.refill_id, "kari", r.version)
    world.service.record_purchase(r.refill_id, "kari", nok(400), "r.jpg")
    # kari is now owed 1 600 > 1 500: she is skipped even though it would be "her turn" again later
    stats = world.service.stats_for("kari")
    assert stats.outstanding["NOK"] == nok(1600)
    world.service.report_empty("OSL", "erik")
    assert "kari" not in {o.employee_id for o in world.service.active_refill("OSL").offers}


def test_fairness_window_is_counted_from_the_office_local_date(world):
    world.complete_refill("OSL", nok(800))  # kari, purchased 23 Nov 2026
    assert world.service.stats_for("kari", today=date(2026, 11, 23)).refills_in_window == 1
    assert world.service.stats_for("kari", today=date(2027, 2, 21)).refills_in_window == 1  # day 90
    assert world.service.stats_for("kari", today=date(2027, 2, 22)).refills_in_window == 0  # day 91


# --- re-review: new paths ------------------------------------------------------
def test_second_claimant_gets_their_own_buy_reminder(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.clock.set(oslo_time(2026, 11, 24, 15, 0))
    world.service.tick()  # kari reminded
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()  # kari released, nils offered
    world.service.accept(r.refill_id, "nils", r.version)  # due Wed 17:00
    world.clock.set(oslo_time(2026, 11, 25, 15, 0))
    world.service.tick()
    assert len([m for m in world.chat.dms_to("nils") if m.startswith("Reminder")]) == 1


def test_stalled_broadcast_does_not_restart_on_a_weekend(world):
    world.clock.set(oslo_time(2026, 11, 27, 10, 0))  # Friday
    r = escalate_oslo(world)
    coordinator_msgs = len(world.chat.dms_to("oslo-coord"))
    world.clock.set(oslo_time(2026, 11, 28, 9, 0))  # Saturday
    world.service.tick()
    assert r.state == S.ESCALATED
    assert len(world.chat.dms_to("oslo-coord")) == coordinator_msgs  # no weekend noise
    world.clock.set(oslo_time(2026, 11, 30, 9, 0))  # Monday
    world.service.tick()
    assert r.state == S.OFFERED
    assert any("asking volunteers one by one again" in p for p in world.chat.channel_posts("OSL"))


def test_rotation_ranks_with_the_office_local_date(world):
    """At 20:30 in Oslo it is already 01:00 the next day in Bengaluru. The 90-day window must
    use the Bengaluru date, which here decides who is asked first."""
    from conftest import inr

    world.registry.set_away("priya", date(2026, 8, 1), date(2026, 12, 31))
    world.clock.set(oslo_time(2026, 8, 25, 9))
    world.complete_refill("BLR", inr(500), reporter="ravi", reimburse=False)  # arjun, still owed
    world.clock.set(oslo_time(2026, 8, 27, 9))  # (26 Aug is a Karnataka holiday)
    world.complete_refill("BLR", inr(500), reporter="ravi")  # meera
    world.clock.set(oslo_time(2026, 11, 23, 20, 30))  # Oslo 23 Nov, Bengaluru 24 Nov
    r = world.service.report_empty("BLR", "ravi")
    # BLR date 24 Nov: arjun's 25 Aug refill is outside the window (0 vs meera's 1), so arjun.
    # With the Oslo date he would tie with meera and lose on money owed.
    assert r.assignee == "arjun"


def test_late_receipt_while_someone_else_is_shopping_warns_them(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()
    world.service.accept(r.refill_id, "nils", r.version)  # nils is about to shop
    world.service.record_purchase(r.refill_id, "kari", nok(700), "late.jpg")
    assert r.state == S.CLAIMED  # a person decides: nils may already be at the till
    assert "check the box before buying" in world.chat.dms_to("nils")[-1]
    assert f"[Cancel original refill] (refill {r.refill_id})" in world.chat.dms_to("oslo-coord")[-1]


def test_late_receipt_must_be_dated_before_the_refill_was_passed_on(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()  # passed on Tue 24 Nov
    world.clock.set(oslo_time(2026, 11, 26, 10, 0))
    with pytest.raises(ValueError, match="after refill"):
        world.service.record_purchase(r.refill_id, "kari", nok(500), "r.jpg", purchase_date=date(2026, 11, 25))
    late = world.service.record_purchase(r.refill_id, "kari", nok(500), "r.jpg", purchase_date=date(2026, 11, 24))
    assert late.state == S.AWAITING_APPROVAL


def test_cancelled_late_receipt_can_be_uploaded_again(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()
    first = world.service.record_purchase(r.refill_id, "kari", nok(500), "blurry.jpg")
    world.service.cancel(first.refill_id, "oslo-coord")  # wrong photo
    second = world.service.record_purchase(r.refill_id, "kari", nok(500), "clear.jpg")
    assert second.refill_id != first.refill_id and second.state == S.AWAITING_APPROVAL


def test_backup_coordinator_can_act_and_nobody_approves_their_own_receipt(world):
    from snack_counter.model import Employee

    world.registry.add(Employee("oslo-coord", "Coordinator", "OSL", "NOK"))  # also a volunteer
    for who in ("kari", "nils", "ola"):
        world.registry.set_away(who, date(2026, 11, 23), date(2026, 11, 23))
    r = world.service.report_empty("OSL", "erik")
    assert r.assignee == "oslo-coord"
    world.service.accept(r.refill_id, "oslo-coord", r.version)
    world.service.record_purchase(r.refill_id, "oslo-coord", nok(1300), "big.jpg")  # over cap
    with pytest.raises(NotAuthorized, match="own receipt"):
        world.service.approve_over_cap(r.refill_id, "oslo-coord")
    world.service.approve_over_cap(r.refill_id, "oslo-backup")
    assert r.state == S.CLAIM_SUBMITTED


def test_not_in_office_today_button_declines_and_fixes_the_schedule(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.decline(r.refill_id, "kari", not_in_office=True)
    assert r.assignee == "nils"
    assert world.registry.location_on("kari", date(2026, 11, 23)) is None  # away today
    world.service.accept(r.refill_id, "nils", r.version)
    world.service.record_purchase(r.refill_id, "nils", nok(600), "r.jpg")

    again = world.service.report_empty("OSL", "erik")  # same day: kari is not asked again
    assert again.assignee == "ola"
    assert world.registry.location_on("kari", date(2026, 11, 24)) == "OSL"  # back tomorrow
