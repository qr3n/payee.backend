"""
Stars calculation utility.
Converts arbitrary monetary amounts into Telegram Stars integer counts.
"""

from decimal import Decimal

from app.core.config import settings

MIN_STARS_AMOUNT = 50
MAX_STARS_AMOUNT = 30000


def calculate_stars_from_amount(
    amount: Decimal,
    currency: str = "RUB",  # noqa: ARG001
    rate: float | None = None,
) -> int:
    """
    Calculate required stars count from payment amount.
    Currently applies 1:1 conversion (or custom rate), clamped between 50 and 30,000.
    """
    applied_rate = Decimal(str(rate or settings.STARS_CALCULATION_RATE))
    calculated_stars = int(amount * applied_rate)

    if calculated_stars < MIN_STARS_AMOUNT:
        return MIN_STARS_AMOUNT
    if calculated_stars > MAX_STARS_AMOUNT:
        return MAX_STARS_AMOUNT
    return calculated_stars
