"""Claims reach HR exactly once, and every HR outcome is handled."""

import pytest

from snack_counter.model import RefillState as S
from snack_counter.money import Money
from snack_counter.refills import InvalidTransition, NotAuthorized

from conftest import nok, oslo_time


def bought(world, amount=nok(800)):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, r.assignee, r.version)
    world.service.record_purchase(r.refill_id, r.assignee, amount, "receipt.jpg")
    return r


def test_hr_outage_is_retried_and_creates_exactly_one_claim(world):
    world.hr.fail_next(2)
    r = bought(world)
    assert r.state == S.PURCHASED and r.submit_pending
    assert world.hr.claims == {}

    world.service.tick()  # still down
    assert world.hr.claims == {}
    world.service.tick()  # back up
    assert r.state == S.CLAIM_SUBMITTED
    world.service.tick()
    world.service.tick()
    assert len(world.hr.claims) == 1


def test_rejected_claim_keeps_the_spend_and_can_be_resubmitted_once(world):
    r = bought(world)
    first_claim = r.hr_claim_id
    world.hr.reject(first_claim, "Receipt is unreadable")
    world.service.sync_hr_status()

    assert r.state == S.REJECTED
    assert world.ledger.spent("OSL", "2026-11") == nok(800)  # snacks were bought and eaten
    assert "unreadable" in world.chat.dms_to("kari")[-1]
    assert "unreadable" in world.chat.dms_to("oslo-coord")[-1]
    assert world.service.stats_for("kari").outstanding["NOK"] == nok(800)  # still owed

    world.service.resubmit(r.refill_id, "kari", "receipt-clear.jpg")
    assert r.state == S.CLAIM_SUBMITTED
    assert r.previous_claim_ids == [first_claim]
    assert len(world.hr.claims) == 2  # the rejected one plus exactly one new one
    assert world.hr.claims[r.hr_claim_id].receipt_ref == "receipt-clear.jpg"


def test_coordinator_can_cancel_a_rejected_claim_which_releases_the_budget(world):
    r = bought(world)
    world.hr.reject(r.hr_claim_id, "Not a snack purchase")
    world.service.sync_hr_status()

    with pytest.raises(NotAuthorized):
        world.service.cancel(r.refill_id, "kari")
    world.service.cancel(r.refill_id, "oslo-coord")
    assert r.state == S.CANCELLED
    assert world.ledger.spent("OSL", "2026-11") == nok(0)


def test_reimbursed_refill_cannot_be_cancelled(world):
    r = world.complete_refill("OSL", nok(500))
    with pytest.raises(InvalidTransition):
        world.service.cancel(r.refill_id, "oslo-coord")


def test_coordinator_is_nudged_once_when_hr_takes_more_than_ten_days(world):
    r = bought(world)
    world.clock.advance(days=9)
    world.service.sync_hr_status()
    assert world.chat.dms_to("oslo-coord") == []

    world.clock.advance(days=2)
    world.service.sync_hr_status()
    world.service.sync_hr_status()
    nudges = [m for m in world.chat.dms_to("oslo-coord") if "waiting at HR" in m]
    assert len(nudges) == 1
    assert r.state == S.CLAIM_SUBMITTED


def test_purchase_without_a_receipt_is_rejected_before_anything_is_booked(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    with pytest.raises(ValueError, match="receipt"):
        world.service.record_purchase(r.refill_id, "kari", nok(500), "  ")
    assert r.state == S.CLAIMED
    assert world.ledger.spent("OSL", "2026-11") == nok(0)
    assert world.hr.claims == {}


def test_resubmit_requires_a_positive_amount(world):
    r = bought(world)
    world.hr.reject(r.hr_claim_id, "Wrong amount")
    world.service.sync_hr_status()
    with pytest.raises(ValueError, match="positive"):
        world.service.resubmit(r.refill_id, "kari", "fixed.jpg", amount=nok(0))
    assert r.state == S.REJECTED and len(world.hr.claims) == 1


def test_coordinator_is_reminded_after_two_working_days_of_an_unresolved_rejection(world):
    r = bought(world)  # Monday 09:00
    world.hr.reject(r.hr_claim_id, "Blurry")
    world.service.sync_hr_status()

    def reminders():
        return [m for m in world.chat.dms_to("oslo-coord") if m.startswith("Reminder")]

    world.clock.set(oslo_time(2026, 11, 24, 16, 59))
    world.service.tick()
    assert reminders() == []
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))  # 16 working hours later
    world.service.tick()
    world.service.tick()
    assert len(reminders()) == 1 and "rejected by HR" in reminders()[0]


def test_chat_outage_never_blocks_a_handover_and_messages_are_retried(world, caplog):
    from snack_counter.fakes import FakeChat

    class FlakyChat(FakeChat):
        down = True

        def send_dm(self, employee_id, text):
            if self.down and employee_id == "kari" and text.startswith("No answer"):
                raise RuntimeError("chat API down")
            super().send_dm(employee_id, text)

    chat = FlakyChat()
    world.service.chat = world.chat = chat
    oslo = world.service.report_empty("OSL", "erik")  # offered to kari, due 11:00 Oslo
    blr = world.service.report_empty("BLR", "ravi")  # offered to arjun, due 15:30 IST = 11:00 Oslo
    world.clock.set(oslo_time(2026, 11, 23, 11, 0))
    world.service.tick()
    assert oslo.assignee == "nils" and blr.assignee == "meera"  # both handed over
    assert "chat delivery failed to kari" in caplog.text
    assert world.service.undelivered_messages == 1  # kept, not lost
    assert not any(m.startswith("No answer") for m in chat.dms_to("kari"))

    chat.down = False
    world.service.tick()  # next tick retries the queued message
    assert world.service.undelivered_messages == 0
    assert chat.dms_to("kari")[-1].startswith("No answer")


def test_hr_sync_failure_on_one_claim_does_not_stop_the_others(world, caplog):
    oslo = bought(world)
    blr = world.service.report_empty("BLR", "ravi")
    world.service.accept(blr.refill_id, blr.assignee, blr.version)
    world.service.record_purchase(blr.refill_id, blr.assignee, Money.of(1000, "INR"), "b.jpg")
    world.hr.reimburse(blr.hr_claim_id)
    real_get_status = world.hr.get_status

    def broken_for_oslo(claim_id):
        if claim_id == oslo.hr_claim_id:
            raise RuntimeError("HR returned garbage")
        return real_get_status(claim_id)

    world.hr.get_status = broken_for_oslo
    world.service.sync_hr_status()
    assert blr.state == S.REIMBURSED  # Bengaluru still processed
    assert oslo.state == S.CLAIM_SUBMITTED
    assert "HR sync failed for refill OSL-0001" in caplog.text


def test_lost_hr_reply_is_never_retried_blindly_coordinator_confirms_existing_claim(world):
    world.hr.fail_next(1, mode="lost_reply_created")  # HR created HR-0001, but the reply was lost
    r = bought(world)
    assert r.state == S.PURCHASED and r.hr_outcome_unknown and not r.submit_pending
    assert len(world.hr.claims) == 1
    assert "may or may not exist" in world.chat.dms_to("oslo-coord")[-1]

    for _ in range(3):
        world.service.tick()  # no automatic resend: that is what would pay twice
    assert len(world.hr.claims) == 1

    with pytest.raises(NotAuthorized):
        world.service.confirm_hr_claim(r.refill_id, "kari", "HR-0001")
    world.service.confirm_hr_claim(r.refill_id, "oslo-coord", "HR-0001")
    assert r.state == S.CLAIM_SUBMITTED and r.hr_claim_id == "HR-0001"
    assert len(world.hr.claims) == 1  # exactly one claim, so exactly one payment


def test_lost_hr_request_coordinator_retries_and_exactly_one_claim_exists(world):
    world.hr.fail_next(1, mode="lost_reply_not_created")
    r = bought(world)
    assert r.hr_outcome_unknown and world.hr.claims == {}
    world.service.retry_hr_submission(r.refill_id, "oslo-coord")
    assert r.state == S.CLAIM_SUBMITTED and not r.hr_outcome_unknown
    assert len(world.hr.claims) == 1
    with pytest.raises(InvalidTransition):  # nothing left to check
        world.service.retry_hr_submission(r.refill_id, "oslo-coord")


def test_coordinator_is_reminded_about_an_unchecked_hr_submission(world):
    world.hr.fail_next(1, mode="lost_reply_created")
    bought(world)  # Monday 09:00
    world.clock.set(oslo_time(2026, 11, 24, 17, 0))
    world.service.tick()
    assert any("check whether HR received" in m for m in world.chat.dms_to("oslo-coord"))


def test_hr_rejecting_an_approved_claim_is_followed_and_flagged(world):
    r = bought(world)
    world.hr.approve(r.hr_claim_id)
    world.service.sync_hr_status()
    assert r.state == S.APPROVED
    world.hr.reject(r.hr_claim_id, "Duplicate of an expense report")
    world.service.sync_hr_status()
    assert r.state == S.REJECTED  # HR decides the money; we follow it
    assert "had approved this claim earlier" in world.chat.dms_to("oslo-coord")[-1]
    assert world.service.stats_for("kari").outstanding["NOK"] == nok(800)  # still owed, visible


def test_confirm_hr_claim_checks_the_id_against_hr_and_other_refills(world):
    other = bought(world)  # HR-0001 belongs to this refill
    world.hr.reimburse(other.hr_claim_id)
    world.service.sync_hr_status()
    world.hr.fail_next(1, mode="lost_reply_created")
    r = bought(world)  # HR-0002 created, reply lost
    for bad, why in (("  ", "required"), ("HR-0O02", "no claim"), ("HR-0001", "already belongs")):
        with pytest.raises(ValueError, match=why):
            world.service.confirm_hr_claim(r.refill_id, "oslo-coord", bad)
    world.service.sync_hr_status()  # a rejected typo never reaches the sync loop
    world.service.confirm_hr_claim(r.refill_id, "oslo-coord", "HR-0002")
    assert r.state == S.CLAIM_SUBMITTED


def test_lost_reply_tells_the_volunteer_and_a_resend_is_marked_for_hr(world):
    world.hr.fail_next(1, mode="lost_reply_not_created")
    r = bought(world)
    assert "coordinator is checking" in world.chat.dms_to("kari")[-1]
    world.hr.fail_next(1, mode="down")  # resend hits an outage: falls back to safe auto-retry
    world.service.retry_hr_submission(r.refill_id, "oslo-coord")
    assert r.submit_pending and world.hr.claims == {}
    world.service.tick()
    assert r.state == S.CLAIM_SUBMITTED and len(world.hr.claims) == 1
    assert "RESUBMISSION" in world.hr.claims[r.hr_claim_id].description


def test_fake_hr_rejects_unknown_failure_modes(world):
    with pytest.raises(ValueError):
        world.hr.fail_next(1, mode="sometimes")
