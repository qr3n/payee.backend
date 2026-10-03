"""
Abstract Base Payment Scenario pattern.
Encapsulates bot interactions, invoice link creation, and external API requests.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.modules.accounts.models import TelegramAccount


@dataclass(slots=True)
class ScenarioContext:
    """Execution context provided to a payment scenario."""

    client_user_id: str
    amount: Decimal
    currency: str
    account: TelegramAccount
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioResult:
    """Outcome produced by a payment scenario execution."""

    payment_link: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparationResult:
    """Outcome of a scenario preparation step."""

    status: str  # "ok", "skipped", "failed"
    reason: str | None = None


class BasePaymentScenario(ABC):
    """
    Abstract contract for bot payment scenarios.
    Each bot or payment provider implements this interface.
    """

    scenario_id: str
    name: str
    description: str = ""
    is_fallback: bool = False
    requires_exclusive_pending_slot: bool = False

    @abstractmethod
    async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
        """
        Execute bot interaction via Telethon / external API
        and return the generated invoice/payment link.
        """
        raise NotImplementedError

    async def cancel_payment(self, _ctx: ScenarioContext) -> None:
        """
        Optional hook to send cancellation/reset command to the bot.
        Default implementation is an intentional no-op.
        """
        return None

    async def prepare(
        self,
        account: TelegramAccount,
        client: Any,
    ) -> PreparationResult:
        """
        Optional hook to warm up and prepare account for this scenario
        in background (e.g., joining channels, /start, subscriptions).
        Default implementation returns skipped.
        """
        del account, client
        return PreparationResult(status="skipped", reason="Not implemented")
