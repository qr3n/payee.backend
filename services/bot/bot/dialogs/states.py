from aiogram.fsm.state import State, StatesGroup


class MainSG(StatesGroup):
    """States for the main menu dialog flow."""

    menu = State()
    system_status = State()
    about = State()


class AccountsSG(StatesGroup):
    """States for Telegram MTProto accounts management."""

    list_accounts = State()
    account_detail = State()
    add_title = State()
    add_session = State()
    add_proxy = State()


class ScenariosSG(StatesGroup):
    """States for scenario testing and payment generation."""

    list_scenarios = State()
    enter_amount = State()
    payment_result = State()
