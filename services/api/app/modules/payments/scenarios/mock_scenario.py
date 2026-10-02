"""
Mock payment scenario for testing, validation, and demo runs.
"""

from uuid import uuid4

from app.modules.payments.scenarios.base import (
    BasePaymentScenario,
    ScenarioContext,
    ScenarioResult,
)


class MockBotScenario(BasePaymentScenario):
    """
    Mock scenario simulating bot invoice generation.
    Returns a deterministic Telegram payment URL.
    """

    scenario_id = "mock_bot"
    name = "Mock Demo Payment Bot"
    description = (
        "Simulates bot invoice generation without sending MTProto messages."
    )

    async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
        token = uuid4().hex[:12]
        bot_username = ctx.meta.get("bot_username", "TestPaymentBot")
        payment_link = (
            f"https://t.me/{bot_username}?start=pay_{token}_{ctx.amount}_{ctx.currency}"
        )
        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "mock_token": token,
                "account_used": ctx.account.title,
                "payer": ctx.client_user_id,
            },
        )
