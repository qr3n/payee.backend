"""
Domain-specific exceptions for Payments module.
"""

from app.core.exceptions import AppException


class NoAccountsAvailableException(AppException):
    """Raised when all active Telegram accounts in the pool are currently reserved."""

    def __init__(
        self,
        message: str = (
            "All Telegram payment accounts are currently reserved. "
            "Please try again shortly."
        ),
    ) -> None:
        super().__init__(
            message=message,
            code="NO_ACCOUNTS_AVAILABLE",
            status_code=409,
        )


class UnknownScenarioException(AppException):
    """Raised when an requested payment scenario is not registered."""

    def __init__(self, scenario_id: str) -> None:
        super().__init__(
            message=f"Payment scenario '{scenario_id}' is not registered.",
            code="UNKNOWN_SCENARIO",
            status_code=400,
        )
