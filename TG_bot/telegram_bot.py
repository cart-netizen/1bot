from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode

from config import TELEGRAM_TOKEN,TELEGRAM_CHAT_ID

# class TelegramBotNotifier:
#     def __init__(self, token: str, chat_id: str):
#         self.bot = Bot(token=token)
#         self.chat_id = chat_id
#
#     async def send_message(self, text: str):
#         await self.bot.send_message(chat_id=self.chat_id, text=text)

class TelegramBotNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher(storage=MemoryStorage())




    def set_strategy_manager(self, strategy_manager):
        self.strategy_manager = strategy_manager

    def register_handlers(self):
        @self.dp.message(commands={"start", "status"})
        async def handle_start(message: types.Message):
            await message.answer("🤖 Бот работает. Получаете торговые уведомления!")

    async def start_polling(self):
        await self.dp.start_polling(self.bot)