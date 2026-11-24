"""Monthly management report: native totals per office plus one combined total in NOK."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from snack_counter.budget import BudgetLedger
from snack_counter.model import Office, RefillState
from snack_counter.money import Money, total
from snack_counter.refills import RefillService
from snack_counter.working_time import local_date, month_key


@dataclass(frozen=True)
class VolunteerActivity:
    """Who carries the load. Shown to everyone, never used to penalise (declining stays free)."""

    employee_id: str
    offered: int = 0
    accepted: int = 0
    declined: int = 0
    no_answer: int = 0
    not_delivered: int = 0  # accepted, then missed the buy deadline


@dataclass(frozen=True)
class OfficeMonth:
    office: str
    name: str
    budget: Money
    spent: Money
    remaining: Money
    refill_count: int
    top_tags: list[tuple[str, int]]
    avg_days_between_refills: float | None
    volunteer_activity: list[VolunteerActivity] = field(default_factory=list)


@dataclass(frozen=True)
class MonthlyReport:
    month: str
    offices: list[OfficeMonth]
    reporting_currency: str
    fx_rates: Mapping[str, Decimal]  # 1 unit of key currency = rate units of reporting currency
    combined_total: Money

    def render(self) -> str:
        lines = [f"Snack spending report {self.month}", "=" * 40]
        for o in self.offices:
            gap = f"{o.avg_days_between_refills:.1f} days" if o.avg_days_between_refills is not None else "n/a"
            tags = ", ".join(f"{t} ({n})" for t, n in o.top_tags) or "none"
            lines += [
                f"{o.name}: spent {o.spent} of {o.budget} (remaining {o.remaining})",
                f"  refills: {o.refill_count}, average gap: {gap}, top snacks: {tags}",
            ]
            for a in o.volunteer_activity:
                lines.append(
                    f"    {a.employee_id}: offered {a.offered}, accepted {a.accepted}, declined {a.declined}, "
                    f"no answer {a.no_answer}, not delivered {a.not_delivered}"
                )
        rates = ", ".join(f"1 {c} = {r} {self.reporting_currency}" for c, r in self.fx_rates.items())
        lines += ["-" * 40, f"Combined total: {self.combined_total}  (rates: {rates or 'n/a'})"]
        return "\n".join(lines)


def monthly_report(
    service: RefillService,
    ledger: BudgetLedger,
    month: str,
    fx_rates: Mapping[str, Decimal],
    reporting_currency: str = "NOK",
) -> MonthlyReport:
    """`fx_rates` maps each non-reporting currency to the monthly average rate, for example {"INR": Decimal("0.1236")}."""
    rows: list[OfficeMonth] = []
    converted: list[Money] = []
    for office in service.offices.values():
        spent = ledger.spent(office.code, month)
        refills = sorted(
            (
                r
                for r in service.refills(office.code)
                if r.purchase_date
                and month_key(r.purchase_date) == month
                and r.state != RefillState.CANCELLED
            ),
            key=lambda r: r.purchase_date,
        )
        dates = [r.purchase_date for r in refills]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        tags = Counter(t for r in refills for t in r.tags)
        activity = _volunteer_activity(service, office, month)
        rows.append(
            OfficeMonth(
                office=office.code,
                name=office.name,
                budget=office.monthly_budget,
                spent=spent,
                remaining=office.monthly_budget - spent,
                refill_count=len(refills),
                top_tags=tags.most_common(3),
                avg_days_between_refills=sum(gaps) / len(gaps) if gaps else None,
                volunteer_activity=activity,
            )
        )
        if office.currency == reporting_currency:
            converted.append(spent)
        else:
            if office.currency not in fx_rates:
                raise KeyError(f"No {office.currency}->{reporting_currency} rate given for {month}")
            # Convert each office total once, to keep rounding to a single step.
            converted.append(spent.convert(reporting_currency, fx_rates[office.currency]))
    return MonthlyReport(
        month=month,
        offices=rows,
        reporting_currency=reporting_currency,
        fx_rates=dict(fx_rates),
        combined_total=total(converted, reporting_currency),
    )


def _volunteer_activity(service: RefillService, office: Office, month: str) -> list[VolunteerActivity]:
    """Count every offer made in this office during `month` (office-local dates)."""
    counts: dict[str, Counter] = {}
    for refill in service.refills(office.code):
        if refill.late_receipt_of is not None:  # a late receipt is not an offer
            continue
        for offer in refill.offers:
            if month_key(local_date(offer.offered_at, office)) != month:
                continue
            c = counts.setdefault(offer.employee_id, Counter())
            c["offered"] += 1
            if offer.outcome in ("accepted", "released"):
                c["accepted"] += 1
            if offer.outcome == "released":
                c["not_delivered"] += 1
            elif offer.outcome == "declined":
                c["declined"] += 1
            elif offer.outcome == "expired":
                c["no_answer"] += 1
    return [VolunteerActivity(emp, **dict(c)) for emp, c in sorted(counts.items())]
