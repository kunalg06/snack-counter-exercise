# Snack Counter Automation — Design

## 1. What "automate" means here

The costly part isn't the shopping. It's the coordination: noticing the box is empty, deciding who buys, making sure nobody else is buying, and getting the money back. So we automate the **coordination and the paperwork**, and the shopping stays human. The system is a chat bot plus a small service. It takes "empty" reports from anyone and asks **one** available volunteer directly, handing the job on if they don't act. It checks the budget before anyone spends, and it turns a receipt photo into an HR claim at once, tracking it until it's paid.

## 2. From "empty" to "reimbursed"

```
REPORTED ─► OFFERED ──accept──► CLAIMED ──receipt──► PURCHASED ─► CLAIM_SUBMITTED ─► APPROVED ─► REIMBURSED
              │ ▲                  │ buy deadline missed      │ over cap → coordinator approves
  decline/    ▼ │ next candidate   ▼                          └─ HR rejects → fix & resubmit, or coordinator cancels
  timeout   OFFERED ◄──────────  released
              │ nobody left → ESCALATED: coordinators + channel broadcast; rotation restarts next working day
```

| Step | What happens | Timer |
|---|---|---|
| Report | Anyone types `/snack empty` (or scans a QR code on the box). Extra reports join the open refill. | – |
| Offer | The top-ranked eligible volunteer gets a DM with *Accept / Decline*. | 2 working hours |
| Claim | Accepting is an atomic compare-and-set. The purchase cap is reserved in the budget. | Buy by end of next working day, with a reminder 2 h before |
| Receipt | Photo, amount and 1–3 snack tags. The claim goes to HR immediately. | – |
| HR | The service polls the status and DMs the volunteer at each change. | 1–10 days; coordinator nudged after 10 |

## 3. Main parts and the data each keeps

| Part | Job | Data |
|---|---|---|
| **Refill service** | State machine; at most one active refill per office | Refill: state, version, assignee, offer history, cap, amount, receipt, HR claim ID |
| **Volunteer registry** | Where each person is on a given day | Home office, payroll currency, volunteer flag, usual office days, away and visiting dates |
| **Assignment** | Ranks eligible volunteers (no state of its own) | – |
| **Budget ledger** | Monthly budget per office, in the office's currency | One entry per refill: reserved or spent, amount, month |
| **Chat / HR adapters** | Ports to the outside systems; simulated in tests | Pending messages; claim ID ↔ refill |
| **Scheduler and report** | Deadlines, reminders, monthly report | The FX rate used, stored with each report |

## 4. How each problem is prevented

| Problem | Mechanism |
|---|---|
| **Box sits empty; everyone assumes someone else will refill it** | One tap to report, then *one named person* is asked. A channel broadcast is what creates the bystander effect. Silence becomes a timeout, and the timeout moves the job on. Nobody has to chase. |
| **Two people buy on the same day** | Only one active refill per office, and accepting is a compare-and-set, so the second click gets "already taken". If someone whose claim was released uploads a receipt anyway, the original refill is closed, or its current claimant is told to check the box. |
| **A few people do most refills and wait weeks for money** | Rank by fewest refills in 90 days, then least money owed, then longest since the last refill. Anyone owed more than about one purchase is skipped. The claim goes to HR the minute the receipt arrives, so the only wait left is HR's own. |
| **Nobody knows spend or what gets eaten** | Live month-to-date spend per office, plus snack tags and days between refills. The monthly report shows each office in its own currency and one combined NOK total. |

**Rules that cut across all of this:**
- **Visitors.** People can refill the office they are physically in today, from that office's budget.
- **Currency.** The claim is in the receipt currency, and HR converts it to payroll currency, so we only do FX for the management total. Money is stored as integer minor units, and NOK and INR are never added together.
- **Budget.** No offer goes out if less than a typical purchase remains. Over-cap receipts need coordinator approval and are never blocked outright, because the volunteer has already paid.
- **Coordinators.** Each office has a coordinator and a backup. Either can act, but nobody approves their own receipt.
- **HR has no idempotency key.** "Unreachable" is retried automatically. "Sent, no reply" is never resent blindly: a coordinator checks HR first.

## 5. Trade-offs, and what we deliberately do NOT build

**Trade-offs**
- **One volunteer at a time, not first come first served.** It's slower in the best case, but the job always has an owner and fairness is enforced.
- **Volunteers state their own availability** (usual days plus exceptions) instead of us integrating with sensors or calendars. Stale data costs one timeout, and a one-tap *Not in office today* decline fixes it.
- **Declining is free but visible.** The monthly report shows, per person, how often they were offered a refill, accepted, declined, didn't answer, or didn't deliver. We make it visible instead of penalising it, to protect goodwill.
- **Budget is reserved on accept, not on purchase.** Parallel refills can't overspend, at the cost of briefly holding money that may not be spent.
- **Edge cases go to a coordinator, not to more automation.** Late receipts, unconfirmed HR sends and HR rejections all go to a person, with a reminder after 2 working days.

**Not built**
- IoT sensors or scales, receipt OCR, demand prediction, and our own FX or payments. HR owns the money.
- A separate app. Everyone already uses chat.
- **Recommended instead of software:** a purchasing card per office would remove reimbursement, and most of the edge cases above, entirely.

Out of scope for the exercise code: the real chat SDK, the real HR API, persistence and authentication. Fakes implement the same ports (`ChatPort`, `HrPort`, `Clock`). Edge-case behaviour is covered by the tests listed in the [README](../README.md#which-test-proves-which-rule).
