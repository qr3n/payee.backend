"""
Stars calculation utility.
Converts arbitrary monetary amounts into Telegram Stars integer counts.
Validates monetary boundaries and safely allocates unique reservation deltas.
"""

from decimal import Decimal

from app.core.config import settings
from app.core.exceptions import AppException

MIN_STARS_AMOUNT = 50
MAX_STARS_AMOUNT = 30000
MAX_STARS_DELTA = 10


class ReservationExhaustedException(AppException):
    """Raised when all unique stars offsets in range are currently busy."""

    def __init__(
        self,
        message: str = (
            "All unique stars allocations for this scenario are currently busy."
        ),
    ) -> None:
        super().__init__(
            message=message,
            code="RESERVATION_EXHAUSTED",
            status_code=409,
        )


class AllocatedStars(tuple[int, int]):
    """Tuple containing (stars_count, delta) with an accessible owner token."""

    stars: int
    delta: int
    token: str

    def __new__(cls, stars: int, delta: int, token: str) -> "AllocatedStars":
        obj = super().__new__(cls, (stars, delta))
        obj.stars = stars
        obj.delta = delta
        obj.token = token
        return obj


def calculate_stars_from_amount(
    amount: Decimal,
    currency: str = "RUB",  # noqa: ARG001
    rate: float | None = None,
) -> int:
    """
    Calculate required stars count from payment amount.
    Validates range strictly [50, 30000] without silent clamping.
    """
    if not amount.is_finite() or amount <= Decimal("0"):
        raise AppException(
            message="Payment amount must be a positive finite number.",
            code="INVALID_AMOUNT",
            status_code=422,
        )

    applied_rate = Decimal(str(rate or settings.STARS_CALCULATION_RATE))
    calculated_stars = int(amount * applied_rate)

    if calculated_stars < MIN_STARS_AMOUNT or calculated_stars > MAX_STARS_AMOUNT:
        raise AppException(
            message=(
                f"Calculated {calculated_stars} Stars for amount {amount} {currency} "
                f"is outside the allowed range "
                f"[{MIN_STARS_AMOUNT}, {MAX_STARS_AMOUNT}] Stars."
            ),
            code="AMOUNT_OUT_OF_RANGE",
            status_code=422,
        )
    return calculated_stars


async def allocate_unique_stars_for_scenario(
    scenario_id: str,
    base_stars: int,
    max_delta: int = MAX_STARS_DELTA,
    owner_token: str | None = None,
) -> AllocatedStars:
    """
    Find and atomically reserve a unique stars_count for a scenario.
    Tries base_stars + 0, + 1, + 2, ..., + max_delta.
    Raises ReservationExhaustedException when all deltas are occupied.
    """
    from app.modules.payments.scenarios.state import (
        acquire_scenario_stars_reservation,
    )

    for delta in range(max_delta + 1):
        candidate = base_stars + delta
        if candidate > MAX_STARS_AMOUNT:
            break
        token = await acquire_scenario_stars_reservation(
            scenario_id, candidate, owner_token=owner_token
        )
        if token:
            return AllocatedStars(candidate, delta, token)

    raise ReservationExhaustedException()
