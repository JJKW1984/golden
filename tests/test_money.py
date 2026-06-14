import pytest
from decimal import Decimal
from finapp.money import to_cents, to_display


class TestToCents:
    def test_integer_string(self):
        assert to_cents("10") == 1000

    def test_decimal_string(self):
        assert to_cents("10.99") == 1099

    def test_half_up_rounding(self):
        # $10.005 → 1001 (half-up, not banker's round)
        assert to_cents("10.005") == 1001

    def test_half_up_rounding_below(self):
        # $10.004 → 1000
        assert to_cents("10.004") == 1000

    def test_decimal_input(self):
        assert to_cents(Decimal("5.50")) == 550

    def test_zero(self):
        assert to_cents("0.00") == 0

    def test_large_amount(self):
        assert to_cents("99999.99") == 9999999

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            to_cents("-1.00")

    def test_string_with_no_cents(self):
        assert to_cents("100") == 10000


class TestToDisplay:
    def test_basic(self):
        assert to_display(1099) == "$10.99"

    def test_zero(self):
        assert to_display(0) == "$0.00"

    def test_large(self):
        assert to_display(123456789) == "$1,234,567.89"

    def test_negative(self):
        assert to_display(-500) == "-$5.00"

    def test_single_cent(self):
        assert to_display(1) == "$0.01"

    def test_round_trip(self):
        original = "1234.56"
        assert to_display(to_cents(original)) == "$1,234.56"
