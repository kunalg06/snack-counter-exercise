"""Demo: a lost HR reply, end to end. Run: uv run examples/hr_lost_reply.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from conftest import make_world, nok, oslo_time  # noqa: E402

w = make_world()
seen = 0


def step(title):
    global seen
    print(f"\n▶ {title}")
    for m in w.chat.messages[seen:]:
        where = f"#{m.to}" if m.channel else m.to
        print(f"    💬 {where}: {m.text}")
    seen = len(w.chat.messages)


def hr_claims():
    return ", ".join(f"{c.claim_id} ({c.status})" for c in w.hr.claims.values()) or "none"


r = w.service.report_empty("OSL", "erik")
w.service.accept(r.refill_id, "kari", r.version)
step("Kari accepts the refill")

w.hr.fail_next(1, mode="lost_reply_created")  # HR stores the claim, the reply never arrives
w.service.record_purchase(r.refill_id, "kari", nok("845.50"), "receipt.jpg", tags=["chips"])
step("Kari uploads her receipt. HR creates the claim, but its reply is lost")
print(f"    state={r.state}, outcome unknown={r.hr_outcome_unknown} | HR has: {hr_claims()}")

for _ in range(3):
    w.service.tick()
step("Three scheduler ticks later")
print(f"    nothing resent automatically | HR still has: {hr_claims()}")

step("The coordinator mistypes the claim ID")
for bad in ("HR-0O01", "  "):
    try:
        w.service.confirm_hr_claim(r.refill_id, "oslo-coord", bad)
    except Exception as e:
        print(f"    ✋ coordinator types {bad!r}: {type(e).__name__}: {e}")

w.service.confirm_hr_claim(r.refill_id, "oslo-backup", "HR-0001")
step("The backup coordinator finds HR-0001 in HR and confirms it")
print(f"    state={r.state}, claim={r.hr_claim_id} | HR has: {hr_claims()}")

w.clock.set(oslo_time(2026, 11, 25, 10))
w.hr.approve("HR-0001")
w.service.sync_hr_status()
w.hr.reject("HR-0001", "Receipt total doesn't match the amount")
w.service.sync_hr_status()
step("HR approves the claim, then rejects it (we follow HR and flag it)")

w.service.resubmit(r.refill_id, "kari", "receipt-clear.jpg", amount=nok("854.50"))
step("Kari corrects the amount and resubmits")
w.hr.reimburse(r.hr_claim_id)
w.service.sync_hr_status()
step("HR reimburses the corrected claim")
print(f"    final state={r.state} | HR has: {hr_claims()} | Oslo spent: {w.ledger.spent('OSL', '2026-11')}")
