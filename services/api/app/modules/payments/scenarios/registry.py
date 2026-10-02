"""
Registry for payment scenarios.
Allows dynamic lookup and registration of different bot payment strategies.
"""

from app.modules.payments.exceptions import UnknownScenarioException
from app.modules.payments.scenarios.base import BasePaymentScenario
from app.modules.payments.scenarios.helperstars_scenario import (
    HelperStarsBotScenario,
)
from app.modules.payments.scenarios.mock_scenario import MockBotScenario
from app.modules.payments.scenarios.starshoppik_scenario import (
    StarShoppikBotScenario,
)
from app.modules.payments.scenarios.starslly_scenario import StarsllyBotScenario


class ScenarioRegistry:
    """Thread-safe registry mapping scenario_id to BasePaymentScenario instances."""

    def __init__(self) -> None:
        self._scenarios: dict[str, BasePaymentScenario] = {}
        # Pre-register default scenarios
        self.register(MockBotScenario())
        self.register(StarsllyBotScenario())
        self.register(StarShoppikBotScenario())
        self.register(HelperStarsBotScenario())

    def register(self, scenario: BasePaymentScenario) -> None:
        """Register a new payment scenario instance."""
        self._scenarios[scenario.scenario_id] = scenario

    def get(self, scenario_id: str) -> BasePaymentScenario:
        """Retrieve scenario by ID or raise UnknownScenarioException."""
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise UnknownScenarioException(scenario_id)
        return scenario

    def list(self) -> list[BasePaymentScenario]:
        """Return all registered scenarios."""
        return list(self._scenarios.values())


scenario_registry = ScenarioRegistry()
