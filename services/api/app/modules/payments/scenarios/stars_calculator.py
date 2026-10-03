"""
Stars calculation utility.
Converts arbitrary monetary amounts into Telegram Stars integer counts.
"""

from decimal import Decimal

from app.core.config import settings

MIN_STARS_AMOUNT = 50
MAX_STARS_AMOUNT = 30000
MAX_STARS_DELTA = 10


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


async def allocate_unique_stars_for_scenario(
    scenario_id: str,
    base_stars: int,
    max_delta: int = MAX_STARS_DELTA,
) -> tuple[int, int]:
    """
    Find and atomically reserve a unique stars_count for a scenario.
    Tries base_stars + 0, + 1, + 2, ..., + max_delta.
    Returns (allocated_stars, delta).
    """
    from app.modules.payments.scenarios.state import (
        acquire_scenario_stars_reservation,
    )

    for delta in range(max_delta + 1):
        candidate = base_stars + delta
        if candidate > MAX_STARS_AMOUNT:
            break
        if await acquire_scenario_stars_reservation(scenario_id, candidate):
            return candidate, delta

    # If all positive deltas are occupied, return base_stars as fallback
    return base_stars, 0
