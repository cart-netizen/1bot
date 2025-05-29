import os
from dotenv import load_dotenv
import logging

load_dotenv()

# Bybit API Credentials
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# Bot Settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DATABASE_PATH = os.getenv("DATABASE_PATH", "trades.db")
MONITORING_INTERVAL_SECONDS = 300  # 5 минут
MIN_24H_VOLUME_USDT = 1_000_000
LEVERAGE = 10

# Проверка наличия ключевых переменных окружения
if not API_KEY or not API_SECRET:
    # Вместо print лучше использовать логгер, но на этом этапе он может быть еще не настроен
    print("ОШИБКА: API_KEY и API_SECRET должны быть установлены в .env файле!")
    # В реальном приложении здесь может быть sys.exit(1) или выброс исключения
    # Для примера продолжим, но бот не сможет торговать

# Настройки для GUI (можно добавить позже)
# ...

# Настройки стратегий (можно вынести в отдельный файл или секцию)
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Настройки для подключения к Bybit
BYBIT_BASE_URL_MAINNET = "https://api.bybit.com"
BYBIT_BASE_URL_TESTNET = "https://api-testnet.bybit.com" # Для тестирования
# Выбираем, какой URL использовать (например, на основе переменной окружения)
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "False"
BYBIT_API_URL = BYBIT_BASE_URL_TESTNET if USE_TESTNET else BYBIT_BASE_URL_MAINNET

# WebSocket
BYBIT_WS_URL_PUBLIC_MAINNET = "wss://stream.bybit.com/v5/public/linear"
BYBIT_WS_URL_PUBLIC_TESTNET = "wss://stream-testnet.bybit.com/v5/public/linear"
BYBIT_WS_URL_PRIVATE_MAINNET = "wss://stream.bybit.com/v5/private"
BYBIT_WS_URL_PRIVATE_TESTNET = "wss://stream-testnet.bybit.com/v5/private"

BYBIT_WS_PUBLIC_URL = BYBIT_WS_URL_PUBLIC_MAINNET if USE_TESTNET else BYBIT_WS_URL_PUBLIC_TESTNET
BYBIT_WS_PRIVATE_URL = BYBIT_WS_URL_PRIVATE_MAINNET if USE_TESTNET else BYBIT_WS_URL_PRIVATE_TESTNET

# Категория для бессрочных контрактов USDT
BYBIT_CATEGORY = "linear" # Для USDT Perpetual

# Логирование
logging.basicConfig(level=LOG_LEVEL,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()]) # Можно добавить FileHandler

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

logger = get_logger(__name__)
logger.info("Конфигурация загружена.")
if USE_TESTNET:
    logger.warning("Бот работает в режиме ТЕСТНЕТА.")
else:
    logger.warning("Бот работает в режиме РЕАЛЬНОЙ ТОРГОВЛИ (MAINNET). Будьте осторожны!")

TELEGRAM_TOKEN = "7815778217:AAEV7XyWuOlN-gGoe1y0dHBSS2LVrBe3uYg"
TELEGRAM_CHAT_ID = "7815778217"

# ML Strategy Parameters
ML_STRATEGY_CONFIDENCE_THRESHOLD = 0.65
ML_STRATEGY_RETRAIN_INTERVAL = 86400  # 24 часа в секундах
ML_STRATEGY_MIN_DATA_POINTS = 1000