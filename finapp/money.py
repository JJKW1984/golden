from decimal import Decimal, ROUND_HALF_UP


def to_cents(value: str | Decimal) -> int:
    """Parse a dollar amount string or Decimal to integer cents. Rounds half-up."""
    d = Decimal(str(value))
    if d < 0:
        raise ValueError(f"Negative amounts not allowed: {value}")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_display(cents: int) -> str:
    """Format integer cents as '$1,234.56'. Negative values prefixed with '-'."""
    negative = cents < 0
    abs_val = abs(cents)
    dollars, remainder = divmod(abs_val, 100)
    result = f"${dollars:,}.{remainder:02d}"
    return f"-{result}" if negative else result
