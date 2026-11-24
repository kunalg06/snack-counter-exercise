"""Walkthrough demo: one scripted stretch of a month across both offices.

Run:  uv run main.py
Every chat message the bot sends is printed, so you can follow a refill from "empty" to "reimbursed".
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from snack_counter.budget import BudgetLedger
from snack_counter.config import DEFAULT_OFFICES
from snack_counter.fakes import FakeChat, FakeClock, FakeHr
from snack_counter.model import Employee, Registry
from snack_counter.money import Money
from snack_counter.refills import AlreadyClaimed, RefillService
from snack_counter.reporting import monthly_report

OSLO = ZoneInfo("Europe/Oslo")


class PrintingChat(FakeChat):
    def send_dm(self, employee_id: str, text: str) -> None:
        super().send_dm(employee_id, text)
        print(f"    💬 DM to {employee_id}: {text}")

    def post_channel(self, office_code: str, text: str) -> None:
        super().post_channel(office_code, text)
        print(f"    📣 #{office_code}-snacks: {text}")


def step(title: str) -> None:
    print(f"\n▶ {title}")


def main() -> None:
    clock = FakeClock(datetime(2026, 11, 23, 9, 0, tzinfo=OSLO))
    chat, hr = PrintingChat(), FakeHr()
    registry = Registry(DEFAULT_OFFICES)
    for emp_id in ("kari", "nils", "ola"):
        registry.add(Employee(emp_id, emp_id.title(), "OSL", "NOK"))
    for emp_id in ("arjun", "meera", "priya"):
        registry.add(Employee(emp_id, emp_id.title(), "BLR", "INR"))
    ledger = BudgetLedger(DEFAULT_OFFICES)
    svc = RefillService(DEFAULT_OFFICES.values(), registry, ledger, chat, hr, clock)

    step("Mon 09:00 Oslo: Erik reports the box empty; Nils reports it too a minute later")
    r = svc.report_empty("OSL", "erik")
    svc.report_empty("OSL", "nils")
    print(f"    -> one refill {r.refill_id}, reporters {r.reporters}, offered to {r.assignee}")

    step("Kari says nothing. At 11:00 her offer expires and the refill moves on")
    clock.set(datetime(2026, 11, 23, 11, 0, tzinfo=OSLO))
    svc.tick()

    step("Nils accepts; Ola clicks an old button at the same moment")
    v = r.version
    svc.accept(r.refill_id, "nils", v)
    try:
        svc.accept(r.refill_id, "ola", v)
    except Exception as e:  # NotOfferedToYou / AlreadyClaimed
        print(f"    ✋ ola: {type(e).__name__}: {e}")
    print(f"    budget: reserved {ledger.reserved('OSL', '2026-11')}, remaining {ledger.remaining('OSL', '2026-11')}")

    step("Nils buys for 845.50 NOK and uploads the receipt; the claim goes to HR straight away")
    svc.record_purchase(r.refill_id, "nils", Money.of("845.50", "NOK"), "receipt-001.jpg", tags=["chips", "fruit"])

    step("Bengaluru: Priya is visiting Oslo this week, so she isn't asked")
    registry.set_visiting("priya", "OSL", date(2026, 11, 23), date(2026, 12, 4))
    b = svc.report_empty("BLR", "ravi")
    svc.decline(b.refill_id, b.assignee)
    svc.decline(b.refill_id, b.assignee)
    print(f"    -> {b.state}: priya was never asked (tried: {sorted(b.tried)})")

    step("Escalated broadcast: Arjun and Meera both click Accept; exactly one wins")
    v = b.version
    svc.accept(b.refill_id, "meera", v)
    try:
        svc.accept(b.refill_id, "arjun", v)
    except AlreadyClaimed as e:
        print(f"    ✋ arjun: {e}")
    svc.record_purchase(b.refill_id, "meera", Money.of(2400, "INR"), "receipt-blr.jpg", tags=["samosa", "biscuits"])

    step("Later in the week, Priya refills the Oslo box: Oslo budget in NOK; HR pays her in INR")
    clock.set(datetime(2026, 11, 26, 10, 0, tzinfo=OSLO))
    for who in ("kari", "nils", "ola"):
        registry.set_away(who, date(2026, 11, 26), date(2026, 11, 26))
    p = svc.report_empty("OSL", "erik")
    svc.accept(p.refill_id, "priya", p.version)
    svc.record_purchase(p.refill_id, "priya", Money.of(500, "NOK"), "receipt-002.jpg", tags=["chips"])

    step("HR works through the claims (1 to 10 days); the bot polls and tells people")
    clock.set(datetime(2026, 11, 30, 10, 0, tzinfo=OSLO))
    hr.reimburse(r.hr_claim_id)
    hr.reject(b.hr_claim_id, "Receipt photo is blurry")
    hr.approve(p.hr_claim_id)
    svc.sync_hr_status()

    step("Meera resubmits a clear photo; HR pays")
    svc.resubmit(b.refill_id, "meera", "receipt-blr-clear.jpg")
    hr.reimburse(b.hr_claim_id)
    hr.reimburse(p.hr_claim_id)
    svc.sync_hr_status()

    step("Monthly report (INR->NOK monthly average rate supplied by finance)")
    print()
    print(monthly_report(svc, ledger, "2026-11", {"INR": Decimal("0.1240")}).render())


if __name__ == "__main__":
    main()
