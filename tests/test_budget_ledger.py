import pytest

from snack_counter.budget import BudgetLedger
from snack_counter.config import DEFAULT_OFFICES
from snack_counter.money import CurrencyMismatch

from conftest import inr, nok


@pytest.fixture
def ledger():
    return BudgetLedger(DEFAULT_OFFICES)


def test_fresh_month_has_full_budget(ledger):
    assert ledger.remaining("OSL", "2026-11") == nok(6000)
    assert ledger.remaining("BLR", "2026-11") == inr(15000)


def test_reserve_settle_release(ledger):
    ledger.reserve("r1", "OSL", "2026-11", nok(1200))
    assert ledger.reserved("OSL", "2026-11") == nok(1200)
    assert ledger.remaining("OSL", "2026-11") == nok(4800)

    ledger.settle("r1", nok(850), "2026-11")
    assert ledger.reserved("OSL", "2026-11") == nok(0)
    assert ledger.spent("OSL", "2026-11") == nok(850)
    assert ledger.remaining("OSL", "2026-11") == nok(5150)

    ledger.release("r1")
    assert ledger.remaining("OSL", "2026-11") == nok(6000)


def test_settle_moves_spend_to_the_receipt_month(ledger):
    ledger.reserve("r1", "OSL", "2026-11", nok(1200))
    ledger.settle("r1", nok(500), "2026-12")
    assert ledger.spent("OSL", "2026-11") == nok(0)
    assert ledger.spent("OSL", "2026-12") == nok(500)


def test_offices_are_tracked_separately_in_their_own_currency(ledger):
    ledger.reserve("r1", "OSL", "2026-11", nok(1000))
    ledger.settle("r1", nok(1000), "2026-11")
    assert ledger.remaining("BLR", "2026-11") == inr(15000)


def test_wrong_currency_for_office_is_rejected(ledger):
    with pytest.raises(CurrencyMismatch):
        ledger.reserve("r1", "OSL", "2026-11", inr(1000))


def test_one_entry_per_refill(ledger):
    ledger.reserve("r1", "OSL", "2026-11", nok(1000))
    with pytest.raises(ValueError):
        ledger.reserve("r1", "OSL", "2026-11", nok(1000))
