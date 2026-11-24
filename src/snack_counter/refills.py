"""Refill lifecycle: REPORTED -> OFFERED -> CLAIMED -> PURCHASED -> CLAIM_SUBMITTED -> APPROVED -> REIMBURSED.

Rules enforced here:
- At most one active refill per office (duplicate "empty" reports merge into it).
- A refill is offered to ONE volunteer at a time; silence becomes a timeout, and the
  timeout hands it to the next candidate. When nobody is left, it escalates, and the
  rotation restarts on the next working day.
- Accepting is an atomic compare-and-set: two people can never both commit.
- The budget is checked before anyone is asked, and the cap is reserved on accept.
- A claim goes to HR as soon as the receipt arrives, and never twice for the same purchase:
  an unconfirmed send is never retried automatically.
- Nobody who spent money is left stranded: a receipt uploaded after the refill was passed
  on goes to a coordinator instead of being rejected, and closes or flags the original.
- Nobody approves their own spending, and a backup coordinator covers holidays.
- State changes commit first; chat messages go through an outbox and are retried until
  delivered, so a chat outage can't leave a refill half-done or a message lost.
"""

from __future__ import annotations

import itertools
import logging
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Iterable, Iterator

from snack_counter.assignment import VolunteerStats, rank_candidates
from snack_counter.budget import BudgetLedger
from snack_counter.model import (
    ACTIVE_STATES,
    OUTSTANDING_STATES,
    PURCHASE_DONE_STATES,
    OfferRecord,
    Office,
    Refill,
    RefillState,
    Registry,
)
from snack_counter.money import CurrencyMismatch, Money
from snack_counter.ports import ChatPort, Clock, HrOutcomeUnknown, HrPort, HrStatus, HrUnavailable
from snack_counter.working_time import (
    WORK_END,
    WORK_START,
    add_working_hours,
    end_of_next_working_day,
    is_working_day,
    local_date,
    month_key,
)

S = RefillState
log = logging.getLogger(__name__)

BUY_REMINDER_BEFORE = timedelta(hours=2)
COORDINATOR_REMINDER_WORKING_DAYS = 2
_WORKING_HOURS_PER_DAY = (
    datetime.combine(date.min, WORK_END) - datetime.combine(date.min, WORK_START)
).seconds / 3600


class RefillError(Exception):
    """Base class for rule violations."""


class AlreadyClaimed(RefillError):
    """Someone else has already committed to this refill."""


class NotOfferedToYou(RefillError):
    """The refill is not (or no longer) offered to this person."""


class StaleVersion(RefillError):
    """The refill changed since the caller last saw it."""


class InvalidTransition(RefillError):
    """The action isn't allowed in the refill's current state."""


class NotAuthorized(RefillError):
    """Only a coordinator (or an eligible volunteer) may do this."""


class RefillService:
    def __init__(
        self,
        offices: Iterable[Office],
        registry: Registry,
        ledger: BudgetLedger,
        chat: ChatPort,
        hr: HrPort,
        clock: Clock,
        *,
        offer_hours: float = 2.0,
        fairness_window_days: int = 90,
        hr_nudge_after_days: int = 10,
    ) -> None:
        self.offices: dict[str, Office] = {o.code: o for o in offices}
        self.registry = registry
        self.ledger = ledger
        self.chat = chat
        self.hr = hr
        self.clock = clock
        self.offer_hours = offer_hours
        self.fairness_window = timedelta(days=fairness_window_days)
        self.hr_nudge_after = timedelta(days=hr_nudge_after_days)
        self._refills: dict[str, Refill] = {}
        self._seq = itertools.count(1)
        # One lock around every state change: makes accept() a true compare-and-set
        # even when two chat button clicks arrive on different threads.
        self._lock = threading.RLock()
        # Messages waiting to be delivered: (kind, recipient, text). See _flush_outbox.
        self._outbox: list[tuple[str, str, str]] = []
        self._outstanding_limits: dict[str, Money] = {}
        for o in self.offices.values():
            existing = self._outstanding_limits.setdefault(o.currency, o.outstanding_limit)
            if existing != o.outstanding_limit:
                raise ValueError(
                    f"Offices sharing {o.currency} must use the same outstanding limit "
                    f"({existing} vs {o.outstanding_limit})"
                )

    # ------------------------------------------------------------------ queries
    def get(self, refill_id: str) -> Refill:
        try:
            return self._refills[refill_id]
        except KeyError:
            raise RefillError(f"Unknown refill {refill_id!r}") from None

    def refills(self, office: str | None = None) -> list[Refill]:
        return [r for r in self._refills.values() if office is None or r.office == office]

    def active_refill(self, office: str) -> Refill | None:
        return next((r for r in self.refills(office) if r.state in ACTIVE_STATES), None)

    @property
    def undelivered_messages(self) -> int:
        return len(self._outbox)

    def stats_for(self, employee_id: str, today: date | None = None) -> VolunteerStats:
        """Refill count in the fairness window, money owed per currency, last refill date.
        `today` should be the office-local date of the office doing the ranking."""
        window_start = (today or self.clock.now().date()) - self.fairness_window
        count, last = 0, None
        owed: dict[str, Money] = {}
        for r in self._refills.values():
            if r.assignee != employee_id or r.purchase_date is None or r.amount is None:
                continue
            if r.state in PURCHASE_DONE_STATES:
                if r.purchase_date >= window_start:
                    count += 1
                last = r.purchase_date if last is None else max(last, r.purchase_date)
            if r.state in OUTSTANDING_STATES:
                cur = r.amount.currency
                owed[cur] = owed.get(cur, Money.zero(cur)) + r.amount
        return VolunteerStats(count, owed, last)

    # ------------------------------------------------------------------ messaging (outbox)
    @contextmanager
    def _command(self) -> Iterator[None]:
        """Every public command: one lock, and the outbox is flushed afterwards, even when
        the command raises (so for example a budget-hold notice still goes out)."""
        with self._lock:
            try:
                yield
            finally:
                self._flush_outbox()

    def _dm(self, employee_id: str | None, text: str) -> None:
        if employee_id:
            self._outbox.append(("dm", employee_id, text))

    def _dm_coordinators(self, office: Office, text: str) -> None:
        for coordinator in office.coordinators:
            self._dm(coordinator, text)

    def _post(self, office_code: str, text: str) -> None:
        self._outbox.append(("channel", office_code, text))

    def _flush_outbox(self) -> None:
        """Deliver queued messages. Failures stay queued and are retried on the next flush
        (every command and every tick): delivery is at-least-once, never lost."""
        pending: list[tuple[str, str, str]] = []
        for kind, to, text in self._outbox:
            try:
                if kind == "dm":
                    self.chat.send_dm(to, text)
                else:
                    self.chat.post_channel(to, text)
            except Exception:
                log.warning("chat delivery failed to %s; will retry", to, exc_info=True)
                pending.append((kind, to, text))
        self._outbox = pending

    # ------------------------------------------------------------------ helpers
    def _office(self, code: str) -> Office:
        try:
            return self.offices[code]
        except KeyError:
            raise RefillError(f"Unknown office {code!r}") from None

    def _today(self, office: Office) -> date:
        return local_date(self.clock.now(), office)

    def _transition(self, refill: Refill, state: RefillState) -> None:
        refill.state = state
        refill.version += 1

    def _require_coordinator(self, refill: Refill, employee_id: str) -> None:
        if employee_id not in self.offices[refill.office].coordinators:
            raise NotAuthorized(f"Only a {refill.office} coordinator can do this")

    def _require_independent_coordinator(self, refill: Refill, employee_id: str) -> None:
        self._require_coordinator(refill, employee_id)
        if employee_id == refill.assignee:
            raise NotAuthorized("You can't approve your own receipt; the other coordinator has to")

    def _over_outstanding_limit(self, stats: VolunteerStats) -> bool:
        return any(
            owed > self._outstanding_limits[cur]
            for cur, owed in stats.outstanding.items()
            if cur in self._outstanding_limits
        )

    def _start_waiting_for_coordinator(self, refill: Refill) -> None:
        refill.waiting_since = self.clock.now()
        refill.coordinator_reminded = False

    @staticmethod
    def _validate_receipt(office: Office, amount: Money, receipt_ref: str) -> None:
        if not receipt_ref or not receipt_ref.strip():
            raise ValueError("A receipt image is required")
        if amount.currency != office.currency:
            raise CurrencyMismatch(f"{office.name} receipts must be in {office.currency}")
        if not amount.is_positive():
            raise ValueError("Purchase amount must be positive")

    def _validate_purchase_date(self, refill: Refill, office: Office, day: date) -> None:
        if day > self._today(office):
            raise ValueError(f"Purchase date {day} is in the future")
        if day < local_date(refill.created_at, office):
            raise ValueError(f"Purchase date {day} is before the box was reported empty")

    # ------------------------------------------------------------------ 1. report
    def report_empty(self, office_code: str, reporter_id: str) -> Refill:
        with self._command():
            office = self._office(office_code)
            active = self.active_refill(office_code)
            if active is not None:  # merge: never two refills for one empty box
                if reporter_id not in active.reporters:
                    active.reporters.append(reporter_id)
                return active
            refill = Refill(
                refill_id=f"{office_code}-{next(self._seq):04d}",
                office=office_code,
                created_at=self.clock.now(),
                reporters=[reporter_id],
            )
            self._refills[refill.refill_id] = refill
            self._offer_next(refill)
            return refill

    # ------------------------------------------------------------------ 2. offer
    def _offer_next(self, refill: Refill) -> None:
        office = self.offices[refill.office]
        today = self._today(office)
        remaining = self.ledger.remaining(office.code, month_key(today))
        if remaining < office.typical_min:
            self._escalate(
                refill, "budget", f"only {remaining} left of this month's {office.monthly_budget}"
            )
            return
        candidates = rank_candidates(
            office,
            today,
            self.registry,
            lambda emp_id: self.stats_for(emp_id, today),
            exclude=refill.tried,
            outstanding_limits=self._outstanding_limits,
        )
        if not candidates:
            self._escalate(refill, "no_candidates", "nobody in the rotation is available")
            return
        chosen = candidates[0]
        now = self.clock.now()
        refill.assignee = chosen.employee_id
        refill.cap = min(office.typical_max, remaining)
        refill.offer_deadline = add_working_hours(now, self.offer_hours, office)
        refill.escalation_reason = None
        refill.offers.append(OfferRecord(chosen.employee_id, now))
        self._transition(refill, S.OFFERED)
        self._dm(
            chosen.employee_id,
            f"The {office.name} snack box is empty. Can you refill it? "
            f"Spend up to {refill.cap}. Please answer by "
            f"{refill.offer_deadline:%a %d %b %H:%M}. [Accept] [Decline] (refill {refill.refill_id})",
        )

    def _escalate(self, refill: Refill, reason: str, detail: str) -> None:
        office = self.offices[refill.office]
        refill.assignee = None
        refill.offer_deadline = None
        refill.escalation_reason = reason
        refill.escalated_at = self.clock.now()
        self._transition(refill, S.ESCALATED)
        self._dm_coordinators(office, f"Refill {refill.refill_id} needs attention: {detail}.")
        if reason == "no_candidates":
            self._post(
                office.code,
                f"The {office.name} snack box is empty and nobody in the rotation is available. "
                f"Any volunteer in the office today who is owed less than {office.outstanding_limit} "
                f"can take it: [Accept] (refill {refill.refill_id})",
            )

    # ------------------------------------------------------------------ 3. accept / decline
    def accept(self, refill_id: str, employee_id: str, expected_version: int) -> Refill:
        """Compare-and-set: succeeds only for the current offeree (or any eligible volunteer
        once escalated), and only if the refill hasn't changed since `expected_version`."""
        with self._command():
            refill = self.get(refill_id)
            office = self.offices[refill.office]
            today = self._today(office)
            if refill.state not in (S.OFFERED, S.ESCALATED):
                if refill.assignee is not None and refill.state not in (S.REPORTED, S.CANCELLED):
                    raise AlreadyClaimed(f"Refill {refill_id} is already taken by {refill.assignee}")
                raise InvalidTransition(f"Refill {refill_id} is {refill.state}")
            if refill.state == S.OFFERED:
                if refill.assignee != employee_id:
                    raise NotOfferedToYou(f"Refill {refill_id} is not offered to {employee_id}")
                if refill.offer_deadline and self.clock.now() >= refill.offer_deadline:
                    raise NotOfferedToYou(f"The offer for refill {refill_id} has expired")
            if refill.state == S.ESCALATED:
                if refill.escalation_reason != "no_candidates":
                    raise InvalidTransition(f"Refill {refill_id} is on hold: {refill.escalation_reason}")
                try:
                    emp = self.registry.get(employee_id)
                except KeyError:
                    raise NotAuthorized(f"Unknown employee {employee_id!r}") from None
                if not emp.is_volunteer or self.registry.location_on(employee_id, today) != office.code:
                    raise NotAuthorized(f"{employee_id} is not a volunteer at {office.name} today")
                if self._over_outstanding_limit(self.stats_for(employee_id, today)):
                    raise NotAuthorized(
                        f"{employee_id} is still owed more than the limit; let someone else take this one"
                    )
            if refill.version != expected_version:
                raise StaleVersion(f"Refill {refill_id} changed; please refresh")

            month = month_key(today)
            remaining = self.ledger.remaining(office.code, month)
            if remaining < office.typical_min:
                if refill.offers and refill.offers[-1].outcome is None:
                    refill.offers[-1].outcome = "budget_hold"
                    refill.offers[-1].closed_at = self.clock.now()
                self._escalate(refill, "budget", f"only {remaining} left this month")
                self._dm(
                    employee_id,
                    f"Sorry, this month's {office.name} snack budget has run out, so refill "
                    f"{refill_id} is on hold. The coordinator has been told; no need to buy anything.",
                )
                raise InvalidTransition(f"Refill {refill_id} is on hold: budget")
            cap = min(office.typical_max, remaining)
            self.ledger.reserve(refill_id, office.code, month, cap)

            now = self.clock.now()
            if refill.offers and refill.offers[-1].employee_id == employee_id and refill.offers[-1].outcome is None:
                refill.offers[-1].outcome = "accepted"
                refill.offers[-1].closed_at = now
            else:  # accepted from the escalation broadcast
                refill.offers.append(OfferRecord(employee_id, now, "accepted", now))
            refill.assignee = employee_id
            refill.cap = cap
            refill.offer_deadline = None
            refill.escalation_reason = None
            refill.buy_deadline = end_of_next_working_day(now, office)
            refill.buy_reminder_sent = False
            self._transition(refill, S.CLAIMED)
            self._dm(
                employee_id,
                f"Thanks! Refill {refill_id} is yours. Spend up to {cap} and upload the receipt "
                f"by {refill.buy_deadline:%a %d %b %H:%M}.",
            )
            return refill

    def decline(self, refill_id: str, employee_id: str, *, not_in_office: bool = False) -> Refill:
        """Decline an offer: free, no reason needed. `not_in_office` is the one-tap
        "Not in office today" button: it also marks the person away for today, so a stale
        schedule fixes itself and nobody asks them again until tomorrow."""
        with self._command():
            refill = self.get(refill_id)
            if refill.state != S.OFFERED or refill.assignee != employee_id:
                raise NotOfferedToYou(f"Refill {refill_id} is not offered to {employee_id}")
            refill.offers[-1].outcome = "declined"
            refill.offers[-1].closed_at = self.clock.now()
            if not_in_office:
                today = self._today(self.offices[refill.office])
                self.registry.set_away(employee_id, today, today)
            self._offer_next(refill)
            return refill

    # ------------------------------------------------------------------ 4. purchase
    def record_purchase(
        self,
        refill_id: str,
        employee_id: str,
        amount: Money,
        receipt_ref: str,
        tags: Iterable[str] = (),
        purchase_date: date | None = None,
    ) -> Refill:
        """Upload a receipt. Returns the refill the receipt was booked on, which is a new
        late-receipt refill when the uploader's claim had already been passed on."""
        with self._command():
            refill = self.get(refill_id)
            office = self.offices[refill.office]
            self._validate_receipt(office, amount, receipt_ref)
            day = purchase_date or self._today(office)
            self._validate_purchase_date(refill, office, day)
            clean_tags = tuple(t.strip().lower() for t in tags if t.strip())
            if refill.assignee != employee_id:
                released = [
                    o for o in refill.offers if o.employee_id == employee_id and o.outcome == "released"
                ]
                if released:  # they bought late: don't strand them, ask a coordinator
                    return self._record_late_receipt(
                        refill, released[-1], employee_id, amount, receipt_ref, clean_tags, day
                    )
                raise NotOfferedToYou(f"Refill {refill_id} is not assigned to {employee_id}")
            if refill.state != S.CLAIMED:
                raise InvalidTransition(f"Refill {refill_id} is {refill.state}, not claimed")
            self.ledger.settle(refill_id, amount, month_key(day))
            refill.amount = amount
            refill.purchase_date = day
            refill.receipt_ref = receipt_ref
            refill.tags = clean_tags
            refill.buy_deadline = None
            self._after_receipt(refill)
            return refill

    def _record_late_receipt(
        self,
        original: Refill,
        released_offer: OfferRecord,
        employee_id: str,
        amount: Money,
        receipt_ref: str,
        tags: tuple[str, ...],
        day: date,
    ) -> Refill:
        office = self.offices[original.office]
        released_on = local_date(released_offer.closed_at or self.clock.now(), office)
        if day > released_on:
            raise ValueError(
                f"Receipt dated {day} is after refill {original.refill_id} was passed on "
                f"({released_on}); it can't be booked against it"
            )
        prefix = f"{original.refill_id}-late-{employee_id}-"
        earlier = [r for r in self._refills.values() if r.refill_id.startswith(prefix)]
        if any(r.state != S.CANCELLED for r in earlier):
            raise InvalidTransition(f"{employee_id} already has a late receipt for {original.refill_id}")
        late_id = f"{prefix}{len(earlier) + 1}"  # a cancelled one (wrong photo) can be re-uploaded
        now = self.clock.now()
        late = Refill(
            refill_id=late_id,
            office=original.office,
            created_at=now,
            reporters=list(original.reporters),
            assignee=employee_id,
            offers=[OfferRecord(employee_id, now, "late receipt", now)],
            cap=amount,
            amount=amount,
            purchase_date=day,
            receipt_ref=receipt_ref,
            tags=tags,
            late_receipt_of=original.refill_id,
        )
        month = month_key(day)
        self.ledger.reserve(late_id, office.code, month, amount)
        self.ledger.settle(late_id, amount, month)  # the money was really spent
        self._refills[late_id] = late
        self._start_waiting_for_coordinator(late)
        self._transition(late, S.AWAITING_APPROVAL)

        # The upload says the box was refilled: deal with the original so nobody buys twice.
        original_note = self._settle_original_after_late_receipt(original, employee_id)
        remaining = self.ledger.remaining(office.code, month)
        self._dm_coordinators(
            office,
            f"{employee_id} bought late for refill {original.refill_id} and uploaded a receipt of "
            f"{amount}. {original_note} Budget left this month: {remaining}. "
            f"[Approve receipt] [Reject receipt] (refill {late_id})"
            + (f" [Cancel original refill] (refill {original.refill_id})" if original.state == S.CLAIMED else ""),
        )
        self._dm(
            employee_id,
            "Thanks! Your receipt arrived after the deadline, so a coordinator will review it "
            "before it goes to HR.",
        )
        return late

    def _settle_original_after_late_receipt(self, original: Refill, late_buyer: str) -> str:
        office = self.offices[original.office]
        if original.state in (S.REPORTED, S.OFFERED, S.ESCALATED):  # nobody shopping yet: close it
            previous = original.assignee
            was_broadcast = original.state == S.ESCALATED
            if original.offers and original.offers[-1].outcome is None:
                original.offers[-1].outcome = "withdrawn"
                original.offers[-1].closed_at = self.clock.now()
            original.offer_deadline = None
            original.cancel_reason = f"box refilled by {late_buyer} (late receipt)"
            self._transition(original, S.CANCELLED)
            self._dm(previous, f"No need to refill: {late_buyer} already did (refill {original.refill_id}). Offer withdrawn.")
            if was_broadcast:
                self._post(office.code, f"The {office.name} snack box has been refilled. The request for refill {original.refill_id} is closed.")
            return "The original refill was still unclaimed, so it was closed automatically."
        if original.state == S.CLAIMED:  # someone may be at the shop: warn, let a person decide
            self._dm(
                original.assignee,
                f"Heads up: {late_buyer} says they already refilled the box (late receipt for "
                f"refill {original.refill_id}). Please check the box before buying. If it's full, "
                f"tell the coordinator so they can cancel your refill.",
            )
            return (
                f"{original.assignee} has claimed the original and was asked to check the box before buying."
            )
        return f"The original refill is already {original.state}."

    def _after_receipt(self, refill: Refill) -> None:
        office = self.offices[refill.office]
        if refill.amount is None or refill.cap is None:
            raise InvalidTransition(f"Refill {refill.refill_id} has no amount or cap")
        if refill.amount > refill.cap:
            self._start_waiting_for_coordinator(refill)
            self._transition(refill, S.AWAITING_APPROVAL)
            self._dm_coordinators(
                office,
                f"Refill {refill.refill_id}: receipt of {refill.amount} from {refill.assignee} is "
                f"above the cap of {refill.cap}. [Approve] [Cancel]",
            )
            self._dm(
                refill.assignee,
                f"Your receipt is above the cap, so a {office.name} coordinator will approve it "
                f"before it goes to HR.",
            )
            return
        self._transition(refill, S.PURCHASED)
        self._submit_claim(refill)

    def approve_over_cap(self, refill_id: str, approver_id: str) -> Refill:
        """A coordinator approves a receipt waiting for approval (over the cap, or late).
        Nobody approves their own receipt."""
        with self._command():
            refill = self.get(refill_id)
            self._require_independent_coordinator(refill, approver_id)
            if refill.state != S.AWAITING_APPROVAL:
                raise InvalidTransition(f"Refill {refill_id} is {refill.state}")
            refill.waiting_since = None
            self._transition(refill, S.PURCHASED)
            self._submit_claim(refill)
            return refill

    # ------------------------------------------------------------------ 5. HR
    def _submit_claim(self, refill: Refill) -> None:
        if refill.hr_claim_id is not None:  # HR isn't idempotent: never submit twice
            return
        office = self.offices[refill.office]
        if not refill.assignee or refill.amount is None or not refill.receipt_ref:
            raise InvalidTransition(f"Refill {refill.refill_id} is missing claim details")
        description = f"Snack refill {refill.refill_id}, {office.name}, {refill.purchase_date}"
        if refill.hr_resend_after_unknown:
            description += " (RESUBMISSION after an unconfirmed send: please check for a duplicate)"
        try:
            claim_id = self.hr.create_claim(
                refill.assignee, refill.amount, description, refill.receipt_ref
            )
        except HrUnavailable:
            refill.submit_pending = True  # never reached HR: tick() retries safely
            return
        except HrOutcomeUnknown:
            # HR may already have the claim. Retrying blindly could pay twice, so a person
            # checks HR (the description carries the refill id) and confirms or resends.
            refill.submit_pending = False
            refill.hr_outcome_unknown = True
            self._start_waiting_for_coordinator(refill)
            self._dm_coordinators(
                office,
                f"HR didn't answer when claim '{description}' was sent, so it may or may not exist. "
                f"Please check HR: [Confirm claim ID] if it's there, [Send again] if it isn't. "
                f"(refill {refill.refill_id})",
            )
            self._dm(
                refill.assignee,
                f"HR didn't confirm receiving your claim for {refill.amount}. A coordinator is "
                f"checking; you don't need to do anything.",
            )
            return
        self._mark_submitted(refill, claim_id)

    def _mark_submitted(self, refill: Refill, claim_id: str) -> None:
        refill.hr_claim_id = claim_id
        refill.submit_pending = False
        refill.hr_outcome_unknown = False
        refill.waiting_since = None
        refill.submitted_at = self.clock.now()
        refill.hr_nudged = False
        self._transition(refill, S.CLAIM_SUBMITTED)
        self._dm(
            refill.assignee,
            f"Your claim for {refill.amount} was sent to HR ({claim_id}). "
            f"HR usually approves within 1 to 10 days.",
        )

    def _require_unknown_submission(self, refill: Refill, coordinator_id: str) -> None:
        self._require_independent_coordinator(refill, coordinator_id)
        if refill.state != S.PURCHASED or not refill.hr_outcome_unknown:
            raise InvalidTransition(f"Refill {refill.refill_id} has no HR submission to check")

    def confirm_hr_claim(self, refill_id: str, coordinator_id: str, claim_id: str) -> Refill:
        """A coordinator found the claim in HR after a lost reply: record it, don't resend.
        The id is checked against HR and must not belong to another refill."""
        with self._command():
            refill = self.get(refill_id)
            self._require_unknown_submission(refill, coordinator_id)
            cid = claim_id.strip()
            if not cid:
                raise ValueError("HR claim ID is required")
            owner = next(
                (r for r in self._refills.values()
                 if r is not refill and (r.hr_claim_id == cid or cid in r.previous_claim_ids)),
                None,
            )
            if owner is not None:
                raise ValueError(f"HR claim {cid} already belongs to refill {owner.refill_id}")
            try:
                self.hr.get_status(cid)
            except (HrUnavailable, HrOutcomeUnknown):
                raise RefillError("HR can't be reached to verify the claim ID; try again later") from None
            except KeyError:
                raise ValueError(f"HR has no claim {cid}; check the ID") from None
            self._mark_submitted(refill, cid)
            return refill

    def retry_hr_submission(self, refill_id: str, coordinator_id: str) -> Refill:
        """A coordinator checked HR after a lost reply and the claim isn't there: send it again.
        The resend is marked in the description so HR can spot a duplicate if the check was wrong."""
        with self._command():
            refill = self.get(refill_id)
            self._require_unknown_submission(refill, coordinator_id)
            refill.hr_outcome_unknown = False
            refill.hr_resend_after_unknown = True
            refill.waiting_since = None
            self._submit_claim(refill)
            return refill

    def sync_hr_status(self) -> None:
        """Poll HR for every open claim (run by the scheduler, for example every 4 hours).
        One claim failing never stops the others from being checked."""
        with self._command():
            now = self.clock.now()
            for refill in list(self._refills.values()):
                if refill.state not in (S.CLAIM_SUBMITTED, S.APPROVED) or refill.hr_claim_id is None:
                    continue
                try:
                    self._sync_one(refill, now)
                except (HrUnavailable, HrOutcomeUnknown):
                    continue  # try again on the next poll
                except Exception:
                    log.exception("HR sync failed for refill %s", refill.refill_id)

    def _sync_one(self, refill: Refill, now: datetime) -> None:
        assert refill.hr_claim_id is not None
        status = self.hr.get_status(refill.hr_claim_id)
        office = self.offices[refill.office]
        if status.status == HrStatus.REIMBURSED:
            self._transition(refill, S.REIMBURSED)
            self._dm(
                refill.assignee,
                f"You've been reimbursed for {refill.amount} (paid in your payroll currency).",
            )
        elif status.status == HrStatus.APPROVED and refill.state == S.CLAIM_SUBMITTED:
            self._transition(refill, S.APPROVED)
            self._dm(refill.assignee, f"HR approved your claim {refill.hr_claim_id}.")
        elif status.status == HrStatus.REJECTED:
            # HR is the source of truth for money: follow it even after an approval,
            # and flag that case to the coordinators.
            was_approved = refill.state == S.APPROVED
            refill.rejection_reason = status.reason
            self._start_waiting_for_coordinator(refill)
            self._transition(refill, S.REJECTED)
            msg = f"HR rejected claim {refill.hr_claim_id} for refill {refill.refill_id}: {status.reason}."
            self._dm(refill.assignee, msg + " Please fix it and resubmit.")
            note = " Note: HR had approved this claim earlier." if was_approved else ""
            self._dm_coordinators(office, msg + note)
        elif (
            refill.state == S.CLAIM_SUBMITTED
            and not refill.hr_nudged
            and refill.submitted_at is not None
            and now - refill.submitted_at > self.hr_nudge_after
        ):
            refill.hr_nudged = True
            self._dm_coordinators(
                office,
                f"Claim {refill.hr_claim_id} for {refill.assignee} has been waiting at HR "
                f"for more than {self.hr_nudge_after.days} days.",
            )

    def resubmit(
        self, refill_id: str, employee_id: str, receipt_ref: str, amount: Money | None = None
    ) -> Refill:
        """After an HR rejection: the volunteer fixes the receipt (and amount) and it goes again."""
        with self._command():
            refill = self.get(refill_id)
            office = self.offices[refill.office]
            if refill.state != S.REJECTED:
                raise InvalidTransition(f"Refill {refill_id} is {refill.state}, not rejected")
            if refill.assignee != employee_id:
                raise NotOfferedToYou(f"Refill {refill_id} belongs to {refill.assignee}")
            if refill.hr_claim_id is None or refill.purchase_date is None or refill.amount is None:
                raise InvalidTransition(f"Refill {refill_id} has no rejected claim to resubmit")
            new_amount = amount if amount is not None else refill.amount
            self._validate_receipt(office, new_amount, receipt_ref)
            if amount is not None:
                self.ledger.settle(refill_id, amount, month_key(refill.purchase_date))
                refill.amount = amount
            refill.previous_claim_ids.append(refill.hr_claim_id)
            refill.hr_claim_id = None
            refill.rejection_reason = None
            refill.waiting_since = None
            refill.receipt_ref = receipt_ref
            self._after_receipt(refill)
            return refill

    def cancel(self, refill_id: str, coordinator_id: str) -> Refill:
        """Coordinator decision: drop the refill and give its budget back."""
        with self._command():
            refill = self.get(refill_id)
            self._require_coordinator(refill, coordinator_id)
            cancellable = ACTIVE_STATES | {S.AWAITING_APPROVAL, S.REJECTED}
            if refill.state not in cancellable:
                raise InvalidTransition(f"Refill {refill_id} is {refill.state}; it can't be cancelled")
            self.ledger.release(refill_id)
            refill.offer_deadline = refill.buy_deadline = None
            refill.waiting_since = None
            refill.cancel_reason = f"cancelled by {coordinator_id}"
            self._transition(refill, S.CANCELLED)
            self._dm(refill.assignee, f"Refill {refill_id} was cancelled by the coordinator.")
            return refill

    # ------------------------------------------------------------------ 6. timers
    def tick(self) -> None:
        """Run by the scheduler (for example every minute): fire deadlines, send reminders,
        restart stalled rotations, retry HR submissions and undelivered messages. One refill's
        failure never stops the others from being processed."""
        with self._command():
            now: datetime = self.clock.now()
            for refill in list(self._refills.values()):
                try:
                    self._tick_refill(refill, now)
                except Exception:
                    log.exception("tick failed for refill %s", refill.refill_id)

    def _tick_refill(self, refill: Refill, now: datetime) -> None:
        office = self.offices[refill.office]
        if refill.state == S.OFFERED and refill.offer_deadline and now >= refill.offer_deadline:
            previous = refill.assignee
            refill.offers[-1].outcome = "expired"
            refill.offers[-1].closed_at = now
            self._offer_next(refill)
            self._dm(previous, f"No answer on refill {refill.refill_id}, so it has been passed on. No problem!")
        elif refill.state == S.CLAIMED and refill.buy_deadline and now >= refill.buy_deadline:
            previous = refill.assignee
            self.ledger.release(refill.refill_id)
            refill.offers[-1].outcome = "released"
            refill.offers[-1].closed_at = now
            refill.buy_deadline = None
            self._offer_next(refill)
            self._dm(
                previous,
                f"Refill {refill.refill_id} wasn't completed in time, so it has been passed on. "
                f"If you already bought snacks, upload the receipt anyway and a coordinator will review it.",
            )
        elif (
            refill.state == S.CLAIMED
            and refill.buy_deadline
            and not refill.buy_reminder_sent
            and now >= refill.buy_deadline - BUY_REMINDER_BEFORE
        ):
            refill.buy_reminder_sent = True
            self._dm(
                refill.assignee,
                f"Reminder: refill {refill.refill_id} is due by {refill.buy_deadline:%a %d %b %H:%M}. "
                f"Upload the receipt before then, or it will be passed on.",
            )
        elif refill.state == S.ESCALATED and refill.escalation_reason == "budget":
            if self.ledger.remaining(office.code, month_key(self._today(office))) >= office.typical_min:
                refill.round_start = now  # for example, a new month: everyone can be asked again
                self._offer_next(refill)
        elif refill.state == S.ESCALATED and refill.escalation_reason == "no_candidates":
            if self._new_working_day_since(refill.escalated_at, now, office):
                refill.round_start = now  # people may be back in the office today
                self._post(
                    office.code,
                    f"Refill {refill.refill_id}: asking volunteers one by one again today; "
                    f"yesterday's open request is closed.",
                )
                self._offer_next(refill)
        elif refill.state == S.PURCHASED and refill.submit_pending:
            self._submit_claim(refill)
        elif (
            (refill.state in (S.AWAITING_APPROVAL, S.REJECTED) or refill.hr_outcome_unknown)
            and refill.waiting_since is not None
            and not refill.coordinator_reminded
            and now >= add_working_hours(
                refill.waiting_since, COORDINATOR_REMINDER_WORKING_DAYS * _WORKING_HOURS_PER_DAY, office
            )
        ):
            refill.coordinator_reminded = True
            if refill.hr_outcome_unknown:
                what = "waiting for you to check whether HR received its claim"
            elif refill.state == S.AWAITING_APPROVAL:
                what = "waiting for your approval"
            else:
                what = "rejected by HR and not resubmitted"
            self._dm_coordinators(
                office,
                f"Reminder: refill {refill.refill_id} ({refill.assignee}, {refill.amount}) has been "
                f"{what} for {COORDINATOR_REMINDER_WORKING_DAYS} working days.",
            )

    @staticmethod
    def _new_working_day_since(since: datetime | None, now: datetime, office: Office) -> bool:
        if since is None:
            return False
        local_now = now.astimezone(office.tz)
        return (
            local_now.date() > local_date(since, office)
            and is_working_day(office, local_now.date())
            and local_now.time() >= WORK_START
        )
