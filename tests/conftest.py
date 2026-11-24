from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from snack_counter.budget import BudgetLedger
from snack_counter.config import DEFAULT_OFFICES
from snack_counter.fakes import FakeChat, FakeClock, FakeHr
from snack_counter.model import Employee, Office, Refill, RefillState, Registry
from snack_counter.money import Money
from snack_counter.refills import RefillService

OSLO_TZ = ZoneInfo("Europe/Oslo")
BLR_TZ = ZoneInfo("Asia/Kolkata")


def oslo_time(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=OSLO_TZ)


def blr_time(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=BLR_TZ)


def nok(x) -> Money:
    return Money.of(x, "NOK")


def inr(x) -> Money:
    return Money.of(x, "INR")


@dataclass
class World:
    offices: dict[str, Office]
    clock: FakeClock
    chat: FakeChat
    hr: FakeHr
    registry: Registry
    ledger: BudgetLedger
    service: RefillService

    def complete_refill(
        self,
        office: str,
        amount: Money,
        reporter: str = "erik",
        tags=(),
        purchase_date=None,
        reimburse: bool = True,
    ) -> Refill:
        """Report, accept by whoever is offered, buy, and (by default) get reimbursed."""
        r = self.service.report_empty(office, reporter)
        assert r.state == RefillState.OFFERED, r.state
        who = r.assignee
        self.service.accept(r.refill_id, who, r.version)
        self.service.record_purchase(
            r.refill_id, who, amount, receipt_ref="receipt.jpg", tags=tags, purchase_date=purchase_date
        )
        if reimburse:
            self.hr.reimburse(r.hr_claim_id)
            self.service.sync_hr_status()
            assert r.state == RefillState.REIMBURSED
        return r


def make_world(start: datetime | None = None) -> World:
    """Monday 23 Nov 2026, 09:00 in Oslo (13:30 in Bengaluru): both offices are open."""
    offices = DEFAULT_OFFICES
    clock = FakeClock(start or oslo_time(2026, 11, 23, 9, 0))
    chat, hr = FakeChat(), FakeHr()
    registry = Registry(offices)
    for emp_id in ("kari", "nils", "ola"):
        registry.add(Employee(emp_id, emp_id.title(), "OSL", "NOK"))
    for emp_id in ("arjun", "meera", "priya"):
        registry.add(Employee(emp_id, emp_id.title(), "BLR", "INR"))
    registry.add(Employee("erik", "Erik", "OSL", "NOK", is_volunteer=False))
    registry.add(Employee("ravi", "Ravi", "BLR", "INR", is_volunteer=False))
    ledger = BudgetLedger(offices)
    service = RefillService(offices.values(), registry, ledger, chat, hr, clock)
    return World(offices, clock, chat, hr, registry, ledger, service)


@pytest.fixture
def world() -> World:
    return make_world()
