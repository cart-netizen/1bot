import logging
from logging.handlers import RotatingFileHandler
import os
from datetime import datetime

def setup_logger(name="bybit_bot"):
    # Создаем папку для логов, если её нет
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Настройка формата логов
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)

    # Создаем логгер
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)  # Уровень INFO (можно изменить на DEBUG для детализации)

    # Логирование в файл (с ротацией)
    file_handler = RotatingFileHandler(
        f"{log_dir}/bybit_bot_{datetime.now().strftime('%Y-%m-%d')}.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Логирование в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# Инициализируем глобальный логгер
logger = setup_logger()