"""Office configuration (values from the brief, plus decisions in docs/questions-and-assumptions.md)."""

from __future__ import annotations

from snack_counter.holidays import KARNATAKA_GENERAL_HOLIDAYS, OSLO_COMPANY_DAYS_OFF, norway_holidays
from snack_counter.model import Office
from snack_counter.money import Money

OSLO = Office(
    code="OSL",
    name="Oslo",
    currency="NOK",
    timezone="Europe/Oslo",
    monthly_budget=Money.of(6000, "NOK"),
    typical_min=Money.of(300, "NOK"),
    typical_max=Money.of(1200, "NOK"),
    outstanding_limit=Money.of(1500, "NOK"),
    coordinator_id="oslo-coord",
    backup_coordinator_id="oslo-backup",
    holidays=norway_holidays(2026) | norway_holidays(2027) | OSLO_COMPANY_DAYS_OFF,
)

BENGALURU = Office(
    code="BLR",
    name="Bengaluru",
    currency="INR",
    timezone="Asia/Kolkata",
    monthly_budget=Money.of(15000, "INR"),
    typical_min=Money.of(500, "INR"),
    typical_max=Money.of(3000, "INR"),
    outstanding_limit=Money.of(4000, "INR"),
    coordinator_id="blr-coord",
    backup_coordinator_id="blr-backup",
    holidays=KARNATAKA_GENERAL_HOLIDAYS,
)

DEFAULT_OFFICES: dict[str, Office] = {o.code: o for o in (OSLO, BENGALURU)}
