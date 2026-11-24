from decimal import Decimal

import pytest

from snack_counter.money import CurrencyMismatch, Money, total


def test_money_is_stored_in_minor_units():
    assert Money.of("299.50", "NOK") == Money(29950, "NOK")
    assert Money.of(1200, "NOK").minor == 120000


def test_too_much_precision_is_rejected():
    with pytest.raises(ValueError):
        Money.of("1.005", "NOK")


def test_same_currency_arithmetic():
    assert Money.of(1000, "NOK") + Money.of(200, "NOK") == Money.of(1200, "NOK")
    assert Money.of(1000, "NOK") - Money.of(200, "NOK") == Money.of(800, "NOK")
    assert Money.of(300, "NOK") < Money.of(301, "NOK")


def test_adding_nok_and_inr_is_an_error():
    with pytest.raises(CurrencyMismatch):
        Money.of(100, "NOK") + Money.of(100, "INR")


def test_comparing_nok_and_inr_is_an_error():
    with pytest.raises(CurrencyMismatch):
        Money.of(100, "NOK") < Money.of(100, "INR")


def test_total_refuses_mixed_currencies():
    with pytest.raises(CurrencyMismatch):
        total([Money.of(1, "NOK"), Money.of(1, "INR")], "NOK")


def test_conversion_is_explicit_and_rounds_half_up():
    assert Money.of(2500, "INR").convert("NOK", Decimal("0.125")) == Money.of("312.50", "NOK")
    # 1.00 INR * 0.1235 = 0.1235 NOK -> rounds half up to 0.12
    assert Money.of(1, "INR").convert("NOK", Decimal("0.1235")) == Money.of("0.12", "NOK")
    assert Money.of(1, "INR").convert("NOK", Decimal("0.125")) == Money.of("0.13", "NOK")
