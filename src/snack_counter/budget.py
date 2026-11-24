"""Monthly budget ledger per office, kept in the office's own currency.

Each refill has at most one entry: a RESERVED cap (from accept until purchase), which
is then SETTLED to the actual SPENT amount in the month on the receipt, or RELEASED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from snack_counter.model import Office
from snack_counter.money import CurrencyMismatch, Money, total


class EntryKind(StrEnum):
    RESERVED = "reserved"
    SPENT = "spent"


@dataclass(frozen=True)
class LedgerEntry:
    refill_id: str
    office: str
    month: str  # "YYYY-MM"
    kind: EntryKind
    amount: Money


class BudgetLedger:
    def __init__(self, offices: dict[str, Office]) -> None:
        self._offices = offices
        self._entries: dict[str, LedgerEntry] = {}

    # --- queries ------------------------------------------------------------
    def budget(self, office: str) -> Money:
        return self._offices[office].monthly_budget

    def _sum(self, office: str, month: str, kinds: set[EntryKind]) -> Money:
        return total(
            (
                e.amount
                for e in self._entries.values()
                if e.office == office and e.month == month and e.kind in kinds
            ),
            self._offices[office].currency,
        )

    def spent(self, office: str, month: str) -> Money:
        return self._sum(office, month, {EntryKind.SPENT})

    def reserved(self, office: str, month: str) -> Money:
        return self._sum(office, month, {EntryKind.RESERVED})

    def remaining(self, office: str, month: str) -> Money:
        """Budget minus spent minus reserved. Can go negative after an approved over-cap purchase."""
        return self.budget(office) - self._sum(office, month, {EntryKind.SPENT, EntryKind.RESERVED})

    def entry(self, refill_id: str) -> LedgerEntry | None:
        return self._entries.get(refill_id)

    # --- commands -----------------------------------------------------------
    def _check_currency(self, office: str, amount: Money) -> None:
        expected = self._offices[office].currency
        if amount.currency != expected:
            raise CurrencyMismatch(f"{office} budget is in {expected}, got {amount.currency}")

    def reserve(self, refill_id: str, office: str, month: str, amount: Money) -> None:
        self._check_currency(office, amount)
        if refill_id in self._entries:
            raise ValueError(f"Refill {refill_id} already has a ledger entry")
        self._entries[refill_id] = LedgerEntry(refill_id, office, month, EntryKind.RESERVED, amount)

    def settle(self, refill_id: str, amount: Money, month: str) -> None:
        """Replace the reservation (or a previous actual) with the real amount, in the receipt's month."""
        existing = self._entries[refill_id]
        self._check_currency(existing.office, amount)
        self._entries[refill_id] = LedgerEntry(
            refill_id, existing.office, month, EntryKind.SPENT, amount
        )

    def release(self, refill_id: str) -> None:
        self._entries.pop(refill_id, None)
