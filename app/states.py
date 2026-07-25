"""Estados de conversa (FSM) do bot."""
from aiogram.fsm.state import State, StatesGroup


class LikeFlow(StatesGroup):
    waiting_id = State()


class InfoFlow(StatesGroup):
    waiting_id = State()


class PurchaseFlow(StatesGroup):
    waiting_id = State()
    confirming = State()


class AdminBroadcast(StatesGroup):
    waiting_message = State()
