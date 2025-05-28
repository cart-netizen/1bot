from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, Optional

from logger_setup import get_logger

logger = get_logger(__name__)

class BaseStrategy(ABC):
    def __init__(self, strategy_name: str, params: Optional[Dict[str, Any]] = None):
        self.strategy_name = strategy_name
        self.params = params if params else {}
        self_logger_name = f"{__name__}.{self.strategy_name}" # Имя логгера с названием стратегии
        self.logger = get_logger(self_logger_name)
        self.logger.info(f"Стратегия '{self.strategy_name}' инициализирована с параметрами: {self.params}")

    @abstractmethod
    async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Генерирует торговые сигналы на основе предоставленных данных.

        Args:
            symbol (str): Торговый символ (например, 'BTCUSDT').
            data (pd.DataFrame): DataFrame с историческими данными (OHLCV) и индикаторами.
                                 Последняя строка - самые свежие данные.

        Returns:
            Optional[Dict[str, Any]]: Словарь с сигналом или None, если сигнала нет.
                Пример сигнала:
                {'signal': 'BUY', 'price': 12345.67, 'confidence': 0.75, 'stop_loss': 12000.00, 'take_profit': 13000.00}
                {'signal': 'SELL', ...}
                {'signal': 'HOLD'} или None
        """
        pass

    def get_params(self) -> Dict[str, Any]:
        return self.params

    def update_params(self, new_params: Dict[str, Any]):
        self.params.update(new_params)
        self.logger.info(f"Параметры стратегии '{self.strategy_name}' обновлены: {self.params}")