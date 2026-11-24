"""Choosing who buys. Pure function: reads where people are and their history; holds no state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Mapping

from snack_counter.model import Employee, Office, Registry
from snack_counter.money import Money


@dataclass(frozen=True)
class VolunteerStats:
    refills_in_window: int = 0
    outstanding: Mapping[str, Money] = field(default_factory=dict)  # currency -> amount owed
    last_refill: date | None = None


def rank_candidates(
    office: Office,
    day: date,
    registry: Registry,
    stats: Callable[[str], VolunteerStats],
    exclude: set[str],
    outstanding_limits: Mapping[str, Money],
) -> list[Employee]:
    """Eligible volunteers for `office` on `day`, best candidate first.

    Eligible: is a volunteer, physically at this office today (home office or visiting),
    not already tried for this refill, and not owed more than the limit in any currency.

    Order: fewest recent refills, then least money currently owed (in this office's
    currency), then longest since their last refill, then employee id (deterministic).
    """
    ranked: list[tuple[tuple, Employee]] = []
    for emp in registry.volunteers_at(office.code, day):
        if emp.employee_id in exclude:
            continue
        s = stats(emp.employee_id)
        if any(
            owed > outstanding_limits[currency]
            for currency, owed in s.outstanding.items()
            if currency in outstanding_limits
        ):
            continue
        owed_here = s.outstanding.get(office.currency, Money.zero(office.currency))
        key = (s.refills_in_window, owed_here.minor, s.last_refill or date.min, emp.employee_id)
        ranked.append((key, emp))
    ranked.sort(key=lambda pair: pair[0])
    return [emp for _, emp in ranked]
