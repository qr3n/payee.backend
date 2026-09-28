from aiogram.fsm.state import State, StatesGroup


class MainSG(StatesGroup):
    """States for the main menu dialog flow."""

    menu = State()
    system_status = State()
    about = State()


class ItemsSG(StatesGroup):
    """States for the items CRUD and interaction dialog flow."""

    list_items = State()
    item_detail = State()
    create_title = State()
    create_description = State()


class AISG(StatesGroup):
    """States for the DeepSeek AI chat dialog flow."""

    chat = State()
