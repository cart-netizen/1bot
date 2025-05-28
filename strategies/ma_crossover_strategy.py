import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, Optional

from strategies.base_strategy import BaseStrategy


class MACrossoverStrategy(BaseStrategy):
  def __init__(self, params: Optional[Dict[str, Any]] = None):
    super().__init__("MA_Crossover", params)
    self.short_ma_period = self.params.get("short_ma_period", 20)
    self.long_ma_period = self.params.get("long_ma_period", 50)
    self.ma_type = self.params.get("ma_type", "sma")  # 'sma' или 'ema'
    self.logger.info(
      f"MA_Crossover_Strategy: Short={self.short_ma_period}, Long={self.long_ma_period}, Type={self.ma_type}")

  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if data.empty or 'close' not in data.columns or len(data) < self.long_ma_period:
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Недостаточно данных или нет 'close'.")
      return None

    close_prices = data['close']

    if self.ma_type.lower() == 'ema':
      short_ma = ta.ema(close_prices, length=self.short_ma_period)
      long_ma = ta.ema(close_prices, length=self.long_ma_period)
    else:  # По умолчанию SMA
      short_ma = ta.sma(close_prices, length=self.short_ma_period)
      long_ma = ta.sma(close_prices, length=self.long_ma_period)

    if short_ma is None or long_ma is None or short_ma.isna().all() or long_ma.isna().all():
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Не удалось рассчитать MA.")
      return None

    # Берем предпоследние и последние значения MA для определения пересечения
    # MA могут иметь NaN в начале, поэтому используем .iloc[-1] и .iloc[-2] после dropna() или на исходных сериях с проверкой

    # Убедимся, что есть хотя бы два значения MA для проверки пересечения
    valid_short_ma = short_ma.dropna()
    valid_long_ma = long_ma.dropna()

    if len(valid_short_ma) < 2 or len(valid_long_ma) < 2:
      self.logger.debug(f"[{self.strategy_name}/{symbol}] Недостаточно значений MA для определения пересечения.")
      return None

    # Последние доступные значения
    last_short_ma = valid_short_ma.iloc[-1]
    prev_short_ma = valid_short_ma.iloc[-2]
    last_long_ma = valid_long_ma.iloc[-1]
    prev_long_ma = valid_long_ma.iloc[-2]

    current_price = data['close'].iloc[-1]
    signal = "HOLD"

    # Бычья стратегия: короткая MA пересекает длинную MA снизу вверх
    if prev_short_ma <= prev_long_ma and last_short_ma > last_long_ma:
      signal = "BUY"
    # Медвежья стратегия: короткая MA пересекает длинную MA сверху вниз
    elif prev_short_ma >= prev_long_ma and last_short_ma < last_long_ma:
      signal = "SELL"

    if signal != "HOLD":
      self.logger.info(f"[{self.strategy_name}/{symbol}] Сгенерирован сигнал: {signal} по цене {current_price}. "
                       f"Short MA: {last_short_ma:.2f}, Long MA: {last_long_ma:.2f}")
      return {"signal": signal, "price": current_price, "strategy_name": self.strategy_name}

    return None