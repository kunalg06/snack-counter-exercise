# Snack Counter Automation

A solution to the Snack Counter exercise. Two offices (Oslo and Bengaluru) keep a volunteer-run snack counter stocked. The aim is that the box never sits empty, nobody buys twice, the work and the money owed are shared fairly, and spending is visible.

**Deliverables**

| Asked for | Where |
|---|---|
| A short design document (1–2 pages) | [`docs/design.md`](docs/design.md) |
| Working core logic, with simulated chat and HR | [`src/snack_counter/`](src/snack_counter/) |
| Automated tests that prove the important rules | [`tests/`](tests/) (102 tests, run in about half a second) |
| Questions I'd ask, and the assumptions I built on | [`docs/questions-and-assumptions.md`](docs/questions-and-assumptions.md) |
| AI tools used, and how | [below](#ai-tools-used-and-how) |

## Run it

You need [uv](https://docs.astral.sh/uv/). It installs the right Python (3.12 or later) and the dependencies.

```bash
uv run pytest                    # all tests
uv run main.py                   # a scripted month in both offices; prints every chat message and the monthly report
uv run examples/hr_lost_reply.py # an HR reply gets lost: how it's handled without paying twice
```

On Windows, run `set PYTHONIOENCODING=utf-8` first so the demo's emoji print.

## Approach

**What "automate" means here.** The costly part isn't the shopping. It's the coordination: noticing the box is empty, deciding who buys, making sure nobody else is also buying, and getting the money back. So I automated the coordination and the paperwork, and left the shopping to people. A chat bot plus a small service does four things:

- Anyone reports "empty" with one tap.
- The service asks **one** named, available volunteer directly. If they don't answer, it hands the job to the next person.
- It checks the budget before anyone spends.
- It turns the receipt photo into an HR claim straight away and tracks it until it's paid.

**How each problem in the brief is handled**

| Problem | Mechanism |
|---|---|
| The box sits empty while everyone assumes someone else will refill it | Ask one named person, not a channel. A broadcast is what creates the bystander effect. Silence turns into a timeout, and the timeout moves the job on. |
| Two people buy on the same day | Only one active refill per office. Accepting is an atomic compare-and-set, so the second click gets "already taken". |
| A few people do most refills and wait weeks for their money | Rank volunteers by fewest recent refills, then by least money currently owed. Anyone already owed more than about one purchase is skipped. The claim goes to HR the minute the receipt arrives. |
| Nobody knows the spend, or what gets eaten | A live ledger per office in its own currency. Each receipt gets snack tags. The monthly report shows each office in its own currency plus one combined NOK total, and who did what. |

**Main decisions.** Each one, with the reasoning, is in the design doc and the questions file.

- **Visitors** refill the office they are physically in, and the money comes from that office's budget. The claim is in the receipt currency, and HR converts it to payroll currency, so we only do FX for the management total.
- **Money** is stored as integer minor units plus a currency. NOK and INR can't be added together.
- **Deadlines** count working hours only, in each office's time zone, minus its public holidays.
- **HR is another company's system and isn't idempotent.** "Unreachable" means the request never left, and it is retried. "Sent, but no reply" is never resent automatically: a coordinator checks HR first. This rules out paying twice.
- **Unusual cases go to a person**, a coordinator with a backup, rather than to more automation. Examples: a late receipt, an over-cap receipt, an HR rejection. Nobody approves their own receipt.
- **Declining is free, but visible** in the monthly report. It protects goodwill without hiding who carries the load.
- **Deliberately not built:** sensors, receipt OCR, demand prediction, our own payments, a separate app. Instead of software, I'd recommend a purchasing card per office, which would remove reimbursement altogether.

## Code map

| File | Responsibility |
|---|---|
| [`refills.py`](src/snack_counter/refills.py) | The refill state machine. Every rule is enforced here. |
| [`assignment.py`](src/snack_counter/assignment.py) | Who is eligible today, and the fairness ranking. A pure function. |
| [`budget.py`](src/snack_counter/budget.py) | Monthly ledger per office: reserve on accept, settle to the receipt amount, or release. |
| [`money.py`](src/snack_counter/money.py) | `Money`: integer minor units and a currency. Conversion is explicit only. |
| [`model.py`](src/snack_counter/model.py) | Offices, employees and where they are each day, refills and their states. |
| [`working_time.py`](src/snack_counter/working_time.py), [`holidays.py`](src/snack_counter/holidays.py) | Working-hours deadlines. Norway's holidays are computed from Easter; Karnataka's are taken from the 2026–27 government lists. |
| [`reporting.py`](src/snack_counter/reporting.py) | Monthly report. |
| [`ports.py`](src/snack_counter/ports.py), [`fakes.py`](src/snack_counter/fakes.py) | Interfaces to chat, HR and the clock, and the in-memory fakes used in tests and demos. |

## Which test proves which rule

| Rule | Tests |
|---|---|
| Two people can't both commit to the same refill | `test_commit_and_handover.py::test_second_accept_is_blocked_while_the_first_is_mid_commit`. This test pauses one caller inside the critical section, and it fails if the lock is removed. |
| A refill nobody completes is handed to someone else | `::test_unanswered_offer_times_out_and_moves_on`, `::test_claimed_refill_not_bought_in_time_is_released_and_reoffered`, `::test_stalled_broadcast_restarts_the_rotation_next_working_day` |
| Budgets and totals are correct across both currencies | `test_budget_rules.py`, `test_reporting.py`, `test_money.py` |
| Fair choice of who buys | `test_assignment.py`, `test_commit_and_handover.py::test_fairness_*` |
| A claim reaches HR exactly once | `test_hr_integration.py::test_hr_outage_is_retried_and_creates_exactly_one_claim`, `::test_lost_hr_reply_is_never_retried_blindly_coordinator_confirms_existing_claim` |
| Nobody who paid is left stranded | `test_commit_and_handover.py::test_late_buyer_is_not_stranded_their_receipt_goes_to_the_coordinator` |

## AI tools used, and how

I used **Claude Code** (Anthropic's coding agent, running the Claude Opus model) in the terminal throughout. I also used the **BMad Method** agent skills on top of it. Here is how each stage worked:

- **Understanding the brief.** I started with a multi-persona discussion: analyst, PM, architect, developer and UX personas debating the brief. That produced the first draft of the design doc and the list of open questions.
- **Questions and decisions.** The brief invites questions. Since this was a take-home, I had the assistant propose an answer to each question, with its reasoning. I reviewed them and accepted or adjusted each one. They are recorded as assumptions, and the ones marked ⚙️ are configuration values.
- **Code and tests.** The assistant wrote the code and tests under my direction, starting from the design. Every rule in the brief has a named test.
- **Review.** I ran two AI code reviews. Each used four independent reviewers: adversarial, edge cases, verification gaps and intent alignment. I triaged each finding and decided on the ambiguous ones. All findings were either fixed or rejected with a reason. I then walked through the code block by block to make sure I could explain it.
- **Checking the tests.** For the key tests, I removed the fix and checked that the test fails. For example, without the lock the concurrency test fails.

The problem framing, the trade-offs and the final decisions are mine, and I can explain every part of the code.

**Time.** This took longer than the suggested 90 minutes. The first pass covered the design, the refill lifecycle, the choice of who buys, the budget check and their tests. The extra time went into the review rounds and the edge cases they surfaced: HR failures, late receipts, chat outages, holidays.

## Known limitations

- **No persistence.** State lives in memory. In production, refills and ledger entries would live in a database, and the accept compare-and-set would be a conditional `UPDATE … WHERE version = ?` instead of a process lock.
- **The chat adapter and the scheduler are simulated.** A real deployment needs a chat adapter that turns button clicks into these method calls, and a scheduler that calls `tick()` every minute and `sync_hr_status()` every few hours.
- **A lost HR reply needs a person.** An idempotency key from the HR vendor would make it fully automatic, and it's the first thing I'd ask them for.
- **Holiday dates are data.** Karnataka's festival dates follow lunar calendars, and Muslim festivals can shift with moon sighting, so in production they would come from the company's HR holiday calendar.
