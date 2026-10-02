"""
Payment Scenarios package.
"""

from app.modules.payments.scenarios.base import (
    BasePaymentScenario,
    ScenarioContext,
    ScenarioResult,
)
from app.modules.payments.scenarios.mock_scenario import MockBotScenario
from app.modules.payments.scenarios.registry import (
    ScenarioRegistry,
    scenario_registry,
)
from app.modules.payments.scenarios.stars_calculator import (
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.starslly_scenario import (
    StarsllyBotScenario,
)

__all__ = [
    "BasePaymentScenario",
    "MockBotScenario",
    "ScenarioContext",
    "ScenarioRegistry",
    "ScenarioResult",
    "StarsllyBotScenario",
    "calculate_stars_from_amount",
    "scenario_registry",
]
