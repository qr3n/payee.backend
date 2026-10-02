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
    choose_add_method = State()

    # Flow 1: Phone Login (Phone -> Code -> 2FA Cloud Password)
    enter_phone = State()
    enter_code = State()
    enter_2fa_password = State()

    # Flow 2: File Upload (.session + .json)
    upload_session_file = State()
    upload_json_file = State()

    # Flow 3: StringSession manual
    add_title = State()
    add_session = State()
    add_proxy = State()


class ScenariosSG(StatesGroup):
    """States for scenario testing and payment generation."""

    list_scenarios = State()
    enter_amount = State()
    payment_result = State()
