"""Money as integer minor units plus an ISO currency code.

Rule: amounts in different currencies are never combined implicitly. Converting
is always explicit, with a rate supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

MINOR_UNITS: dict[str, int] = {"NOK": 2, "INR": 2, "EUR": 2}


class CurrencyMismatch(ValueError):
    """Raised when two amounts in different currencies are combined or compared."""


@dataclass(frozen=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {self.currency!r}")
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise TypeError("Money.minor must be an int (minor units)")

    # --- construction -------------------------------------------------------
    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> Money:
        """Build from a major-unit amount, e.g. Money.of("299.50", "NOK")."""
        if currency not in MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {currency!r}")
        scaled = Decimal(str(amount)) * (10 ** MINOR_UNITS[currency])
        if scaled != scaled.to_integral_value():
            raise ValueError(f"{amount} has more precision than {currency} allows")
        return cls(int(scaled), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    # --- arithmetic & comparison (same currency only) ----------------------
    def _check(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise TypeError(f"Cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} with {other.currency}")
        return other

    def __add__(self, other: Money) -> Money:
        return Money(self.minor + self._check(other).minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(self.minor - self._check(other).minor, self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.minor < self._check(other).minor

    def __le__(self, other: Money) -> bool:
        return self.minor <= self._check(other).minor

    def __gt__(self, other: Money) -> bool:
        return self.minor > self._check(other).minor

    def __ge__(self, other: Money) -> bool:
        return self.minor >= self._check(other).minor

    # --- helpers ------------------------------------------------------------
    @property
    def amount(self) -> Decimal:
        return Decimal(self.minor).scaleb(-MINOR_UNITS[self.currency])

    def is_positive(self) -> bool:
        return self.minor > 0

    def convert(self, to_currency: str, rate: Decimal) -> Money:
        """Explicit conversion: 1 unit of self.currency = `rate` units of to_currency."""
        if to_currency == self.currency:
            return self
        converted = (self.amount * Decimal(rate)).quantize(
            Decimal(1).scaleb(-MINOR_UNITS[to_currency]), rounding=ROUND_HALF_UP
        )
        return Money.of(converted, to_currency)

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"


def total(amounts: Iterable[Money], currency: str) -> Money:
    """Sum amounts that must all be in `currency` (raises CurrencyMismatch otherwise)."""
    result = Money.zero(currency)
    for amount in amounts:
        result = result + amount
    return result
