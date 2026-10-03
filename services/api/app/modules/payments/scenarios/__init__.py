"""
Payment Scenarios package.
"""

from app.modules.payments.scenarios.base import (
    BasePaymentScenario,
    ScenarioContext,
    ScenarioResult,
)
from app.modules.payments.scenarios.helperstars_scenario import (
    HelperStarsBotScenario,
)
from app.modules.payments.scenarios.mock_scenario import MockBotScenario
from app.modules.payments.scenarios.registry import (
    ScenarioRegistry,
    scenario_registry,
)
from app.modules.payments.scenarios.stage_timer import StageTimer
from app.modules.payments.scenarios.stars_calculator import (
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.starshoppik_scenario import (
    StarShoppikBotScenario,
)
from app.modules.payments.scenarios.starslly_scenario import (
    StarsllyBotScenario,
)
from app.modules.payments.scenarios.state import (
    clear_scenario_prepared,
    is_scenario_prepared,
    set_scenario_prepared,
)

__all__ = [
    "BasePaymentScenario",
    "HelperStarsBotScenario",
    "MockBotScenario",
    "ScenarioContext",
    "ScenarioRegistry",
    "ScenarioResult",
    "StageTimer",
    "StarShoppikBotScenario",
    "StarsllyBotScenario",
    "calculate_stars_from_amount",
    "clear_scenario_prepared",
    "is_scenario_prepared",
    "scenario_registry",
    "set_scenario_prepared",
]
