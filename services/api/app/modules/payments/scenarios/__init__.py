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
    allocate_unique_stars_for_scenario,
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.starshoppik_scenario import (
    StarShoppikBotScenario,
)
from app.modules.payments.scenarios.starslly_scenario import (
    StarsllyBotScenario,
)
from app.modules.payments.scenarios.state import (
    acquire_account_generation_lock,
    acquire_scenario_stars_reservation,
    clear_scenario_prepared,
    is_account_generation_locked,
    is_scenario_prepared,
    release_account_generation_lock,
    release_all_account_generation_locks,
    release_all_scenario_stars_reservations,
    release_scenario_stars_reservation,
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
    "acquire_account_generation_lock",
    "acquire_scenario_stars_reservation",
    "allocate_unique_stars_for_scenario",
    "calculate_stars_from_amount",
    "clear_scenario_prepared",
    "is_account_generation_locked",
    "is_scenario_prepared",
    "release_account_generation_lock",
    "release_all_account_generation_locks",
    "release_all_scenario_stars_reservations",
    "release_scenario_stars_reservation",
    "scenario_registry",
    "set_scenario_prepared",
]
