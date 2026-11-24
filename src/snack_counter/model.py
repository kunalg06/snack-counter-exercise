"""Domain model: offices, employees and where they are, refills and their states."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from snack_counter.money import Money
from snack_counter.working_time import is_working_day


@dataclass(frozen=True)
class Office:
    code: str
    name: str
    currency: str
    timezone: str
    monthly_budget: Money
    typical_min: Money
    typical_max: Money
    outstanding_limit: Money  # skip volunteers already owed more than this
    coordinator_id: str
    holidays: frozenset[date] = frozenset()
    backup_coordinator_id: str | None = None  # Q2: covers holidays; either coordinator can act

    @property
    def coordinators(self) -> tuple[str, ...]:
        return tuple(c for c in (self.coordinator_id, self.backup_coordinator_id) if c)

    def __post_init__(self) -> None:
        for name in ("monthly_budget", "typical_min", "typical_max", "outstanding_limit"):
            if getattr(self, name).currency != self.currency:
                raise ValueError(f"{self.code}.{name} must be in {self.currency}")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("DateRange end is before start")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass
class Employee:
    employee_id: str
    name: str
    home_office: str
    payroll_currency: str
    is_volunteer: bool = True
    office_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})  # usual in-office days, Mon=0
    away: list[DateRange] = field(default_factory=list)
    visits: list[tuple[str, DateRange]] = field(default_factory=list)


class Registry:
    """Who exists, and which office (if any) each person is physically in on a given day."""

    def __init__(self, offices: dict[str, Office]) -> None:
        self._offices = offices
        self._employees: dict[str, Employee] = {}

    def add(self, employee: Employee) -> Employee:
        if employee.home_office not in self._offices:
            raise ValueError(f"Unknown office {employee.home_office!r}")
        self._employees[employee.employee_id] = employee
        return employee

    def get(self, employee_id: str) -> Employee:
        return self._employees[employee_id]

    def set_away(self, employee_id: str, start: date, end: date) -> None:
        self.get(employee_id).away.append(DateRange(start, end))

    def set_visiting(self, employee_id: str, office_code: str, start: date, end: date) -> None:
        if office_code not in self._offices:
            raise ValueError(f"Unknown office {office_code!r}")
        self.get(employee_id).visits.append((office_code, DateRange(start, end)))

    def location_on(self, employee_id: str, day: date) -> str | None:
        """Office the person is physically at on `day`, or None (remote, away, non-working day)."""
        emp = self.get(employee_id)
        if any(r.contains(day) for r in emp.away):
            return None
        for office_code, period in emp.visits:
            if period.contains(day):
                return office_code if is_working_day(self._offices[office_code], day) else None
        home = self._offices[emp.home_office]
        if day.weekday() in emp.office_weekdays and is_working_day(home, day):
            return home.code
        return None

    def volunteers_at(self, office_code: str, day: date) -> list[Employee]:
        return [
            e
            for e in self._employees.values()
            if e.is_volunteer and self.location_on(e.employee_id, day) == office_code
        ]


class RefillState(StrEnum):
    REPORTED = "reported"
    OFFERED = "offered"
    CLAIMED = "claimed"
    ESCALATED = "escalated"
    PURCHASED = "purchased"
    AWAITING_APPROVAL = "awaiting_approval"  # receipt above cap, waiting for coordinator
    CLAIM_SUBMITTED = "claim_submitted"
    APPROVED = "approved"
    REIMBURSED = "reimbursed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# The box is still empty in these states: at most one per office.
ACTIVE_STATES = frozenset(
    {RefillState.REPORTED, RefillState.OFFERED, RefillState.CLAIMED, RefillState.ESCALATED}
)
# Volunteer has paid and is still owed money.
OUTSTANDING_STATES = frozenset(
    {
        RefillState.PURCHASED,
        RefillState.AWAITING_APPROVAL,
        RefillState.CLAIM_SUBMITTED,
        RefillState.APPROVED,
        RefillState.REJECTED,
    }
)
# A purchase happened (counts toward the fairness rotation).
PURCHASE_DONE_STATES = OUTSTANDING_STATES | {RefillState.REIMBURSED}


@dataclass
class OfferRecord:
    employee_id: str
    offered_at: datetime
    outcome: str | None = None  # accepted / declined / expired / released / budget_hold
    closed_at: datetime | None = None  # when the outcome was decided (used to bound late receipts)


@dataclass
class Refill:
    refill_id: str
    office: str
    created_at: datetime
    reporters: list[str]
    state: RefillState = RefillState.REPORTED
    version: int = 0  # bumped on every transition; used for compare-and-set
    assignee: str | None = None
    offers: list[OfferRecord] = field(default_factory=list)
    offer_deadline: datetime | None = None
    buy_deadline: datetime | None = None
    escalation_reason: str | None = None  # "budget" | "no_candidates"
    cap: Money | None = None
    amount: Money | None = None
    purchase_date: date | None = None
    receipt_ref: str | None = None
    tags: tuple[str, ...] = ()
    hr_claim_id: str | None = None
    previous_claim_ids: list[str] = field(default_factory=list)
    submit_pending: bool = False  # HR unreachable: safe to retry automatically
    hr_outcome_unknown: bool = False  # sent but no reply: coordinator must check HR before any retry
    submitted_at: datetime | None = None
    hr_nudged: bool = False
    rejection_reason: str | None = None
    buy_reminder_sent: bool = False
    escalated_at: datetime | None = None
    round_start: datetime | None = None  # rotation restarts (new day, budget back) reset `tried`
    waiting_since: datetime | None = None  # started waiting on a coordinator (approval, rejection, HR check)
    coordinator_reminded: bool = False
    late_receipt_of: str | None = None  # set on a receipt uploaded after its refill was passed on
    hr_resend_after_unknown: bool = False  # resent after an unconfirmed send: flagged to HR
    cancel_reason: str | None = None

    @property
    def tried(self) -> set[str]:
        """Everyone offered this refill in the current rotation round (excluded from it)."""
        return {
            o.employee_id
            for o in self.offers
            if self.round_start is None or o.offered_at >= self.round_start
        }
