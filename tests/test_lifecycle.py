"""From 'empty' to 'reimbursed'."""

from snack_counter.model import RefillState as S

from conftest import nok


def test_reporting_empty_offers_the_refill_to_one_volunteer(world):
    r = world.service.report_empty("OSL", "erik")
    assert r.state == S.OFFERED
    assert r.assignee == "kari"  # all equal -> deterministic tie-break by id
    assert r.cap == nok(1200)
    assert len(world.chat.dms_to("kari")) == 1
    assert world.chat.dms_to("nils") == [] and world.chat.dms_to("ola") == []  # not a broadcast


def test_duplicate_empty_reports_merge_into_the_open_refill(world):
    first = world.service.report_empty("OSL", "erik")
    second = world.service.report_empty("OSL", "nils")
    assert second is first
    assert first.reporters == ["erik", "nils"]
    assert len(world.service.refills("OSL")) == 1
    assert len(world.chat.dms_to("kari")) == 1  # no second offer


def test_empty_report_while_someone_is_out_buying_merges_and_sends_nothing(world):
    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    sent_before = len(world.chat.messages)
    assert world.service.report_empty("OSL", "nils") is r
    assert r.state == S.CLAIMED and r.reporters == ["erik", "nils"]
    assert len(world.chat.messages) == sent_before  # nobody else is asked to buy


def test_each_office_has_its_own_active_refill(world):
    oslo = world.service.report_empty("OSL", "erik")
    blr = world.service.report_empty("BLR", "ravi")
    assert oslo is not blr
    assert blr.assignee == "arjun"


def test_happy_path_empty_to_reimbursed(world):
    svc, hr = world.service, world.hr
    r = svc.report_empty("OSL", "erik")
    svc.accept(r.refill_id, "kari", r.version)
    assert r.state == S.CLAIMED
    assert world.ledger.reserved("OSL", "2026-11") == nok(1200)

    svc.record_purchase(r.refill_id, "kari", nok("845.50"), "receipt-1.jpg", tags=["Chips", "fruit"])
    assert r.state == S.CLAIM_SUBMITTED
    claim = hr.claims[r.hr_claim_id]
    assert (claim.employee_id, claim.amount, claim.receipt_ref) == ("kari", nok("845.50"), "receipt-1.jpg")
    assert world.ledger.spent("OSL", "2026-11") == nok("845.50")
    assert world.ledger.reserved("OSL", "2026-11") == nok(0)
    assert r.tags == ("chips", "fruit")

    hr.approve(r.hr_claim_id)
    svc.sync_hr_status()
    assert r.state == S.APPROVED

    hr.reimburse(r.hr_claim_id)
    svc.sync_hr_status()
    assert r.state == S.REIMBURSED

    dms = world.chat.dms_to("kari")
    assert len(dms) == 5  # offer, accepted, sent to HR, approved, reimbursed
    assert "reimbursed" in dms[-1]


def test_box_can_be_reported_empty_again_once_refilled(world):
    first = world.complete_refill("OSL", nok(800))
    second = world.service.report_empty("OSL", "erik")
    assert second is not first
    assert second.state == S.OFFERED


def test_purchase_must_be_in_the_office_currency(world):
    import pytest

    from snack_counter.money import CurrencyMismatch, Money

    r = world.service.report_empty("OSL", "erik")
    world.service.accept(r.refill_id, "kari", r.version)
    with pytest.raises(CurrencyMismatch):
        world.service.record_purchase(r.refill_id, "kari", Money.of(800, "INR"), "x.jpg")
