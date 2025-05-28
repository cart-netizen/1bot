import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import logger

from config import LOG_LEVEL # Импортируем из нашего config.py
from datetime import datetime

def setup_logging(log_level_str: str = LOG_LEVEL) -> None:
    """
    Настраивает основную конфигурацию логирования для всего приложения.
    """
    numeric_level = getattr(logging, log_level_str.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Неверный уровень логирования: {log_level_str}")

    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Базовая конфигурация с выводом в stdout
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - [%(levelname)s] - (%(filename)s).%(funcName)s(%(lineno)d) - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            # Сюда можно добавить FileHandler для записи логов в файл
            #logging.FileHandler(f"{log_dir}/bybit_bot_{datetime.now().strftime('%Y-%m-%d')}.log",
            #mode='a', encoding='utf-8')
        ])
    file_handler = RotatingFileHandler(
        f"{log_dir}/bybit_bot_{datetime.now().strftime('%Y-%m-%d')}.log",
        maxBytes=5 * 1024 * 1024, mode='a', encoding='utf-8', # 5 MB
        backupCount=3,
    )
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)
    #file_handler.setFormatter(formatter)


        # Отключаем излишне подробные логи от некоторых библиотек, если нужно
    # logging.getLogger("websockets").setLevel(logging.WARNING)
    # logging.getLogger("httpx").setLevel(logging.WARNING) # если используется httpx вместо requests/aiohttp

    logger = logging.getLogger(__name__)
    logger.info(f"Логирование настроено на уровень: {log_level_str}")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

def get_logger(name: str) -> logging.Logger:
    """
    Возвращает экземпляр логгера для указанного модуля.
    """
    return logging.getLogger(name)


logger = setup_logging()
# Пример вызова настройки при импорте модуля, если это основной конфигуратор логов
# setup_logging()