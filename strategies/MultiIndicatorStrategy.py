import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, Optional

from strategies.base_strategy import BaseStrategy


class MultiIndicatorStrategy(BaseStrategy):
  def __init__(self, params: Optional[Dict[str, Any]] = None):
    super().__init__("Multi_Indicator", params)

    # MA params
    self.short_ma_period = self.params.get("short_ma_period", 20)
    self.long_ma_period = self.params.get("long_ma_period", 50)
    self.ma_type = self.params.get("ma_type", "sma").lower()  # 'sma' or 'ema'

    # RSI params
    self.rsi_period = self.params.get("rsi_period", 14)
    self.rsi_overbought = self.params.get("rsi_overbought", 70)
    self.rsi_oversold = self.params.get("rsi_oversold", 30)

    # MACD params
    self.macd_fast = self.params.get("macd_fast", 12)
    self.macd_slow = self.params.get("macd_slow", 26)
    self.macd_signal = self.params.get("macd_signal", 9)

    # Stop loss / take profit (percent)
    self.stop_loss_pct = self.params.get("stop_loss_pct", 0.02)  # 2%
    self.take_profit_pct = self.params.get("take_profit_pct", 0.05)  # 5%

    # Minimal volume filter for signals
    self.min_volume = self.params.get("min_volume", 0)

    # Position tracking
    self.position: Optional[str] = None  # "long" or "short" or None
    self.entry_price: Optional[float] = None

    self.logger.info(
      f"MultiIndicatorStrategy initialized with: MA({self.short_ma_period},{self.long_ma_period}, {self.ma_type}), "
      f"RSI({self.rsi_period}, OB={self.rsi_overbought}, OS={self.rsi_oversold}), "
      f"MACD(fast={self.macd_fast}, slow={self.macd_slow}, signal={self.macd_signal}), "
      f"StopLoss={self.stop_loss_pct * 100}%, TakeProfit={self.take_profit_pct * 100}%, "
      f"MinVolume={self.min_volume}")

  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if data.empty or 'close' not in data.columns or len(data) < max(self.long_ma_period, self.rsi_period,
                                                                    self.macd_slow + self.macd_signal):
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Недостаточно данных или нет 'close'.")
      return None

    close = data['close']
    volume = data['volume'] if 'volume' in data.columns else None
    current_price = close.iloc[-1]

    # Проверяем минимальный объем
    if volume is not None and volume.iloc[-1] < self.min_volume:
      self.logger.debug(
        f"[{self.strategy_name}/{symbol}] Объем {volume.iloc[-1]} ниже минимального {self.min_volume}. Сигнал не генерируется.")
      return None

    # Сигналы индикаторов
    ma_signal = self._ma_signal(close)
    rsi_signal = self._rsi_signal(close)
    macd_signal = self._macd_signal(close)

    self.logger.debug(
      f"[{self.strategy_name}/{symbol}] Индикаторы: MA={ma_signal}, RSI={rsi_signal}, MACD={macd_signal}")

    # Согласование сигналов
    if ma_signal == rsi_signal == macd_signal and ma_signal in ["BUY", "SELL"]:
      signal = ma_signal
    else:
      signal = None

    # Проверка и управление открытой позицией
    if self.position and self.entry_price:
      sl_triggered, tp_triggered = self._check_stop_take_profit(current_price)
      if sl_triggered:
        await self._close_position(symbol, current_price, reason="stop_loss")
        return {"signal": "SELL" if self.position == "long" else "BUY", "reason": "stop_loss", "price": current_price}
      if tp_triggered:
        await self._close_position(symbol, current_price, reason="take_profit")
        return {"signal": "SELL" if self.position == "long" else "BUY", "reason": "take_profit", "price": current_price}

    # Если нет позиции, открываем позицию при сигнале
    if not self.position and signal is not None:
      success = await self._open_position(symbol, signal, current_price)
      if success:
        return {"signal": signal, "price": current_price, "strategy_name": self.strategy_name}

    return None

  def _ma_signal(self, close: pd.Series) -> Optional[str]:
    if self.ma_type == 'ema':
      short_ma = ta.ema(close, length=self.short_ma_period)
      long_ma = ta.ema(close, length=self.long_ma_period)
    else:
      short_ma = ta.sma(close, length=self.short_ma_period)
      long_ma = ta.sma(close, length=self.long_ma_period)

    if short_ma.isna().all() or long_ma.isna().all():
      return None

    valid_short = short_ma.dropna()
    valid_long = long_ma.dropna()
    if len(valid_short) < 2 or len(valid_long) < 2:
      return None

    prev_short = valid_short.iloc[-2]
    last_short = valid_short.iloc[-1]
    prev_long = valid_long.iloc[-2]
    last_long = valid_long.iloc[-1]

    if prev_short <= prev_long and last_short > last_long:
      return "BUY"
    elif prev_short >= prev_long and last_short < last_long:
      return "SELL"
    return None

  def _rsi_signal(self, close: pd.Series) -> Optional[str]:
    rsi = ta.rsi(close, length=self.rsi_period)
    if rsi.isna().all():
      return None

    last_rsi = rsi.iloc[-1]
    if last_rsi < self.rsi_oversold:
      return "BUY"
    elif last_rsi > self.rsi_overbought:
      return "SELL"
    return None

  def _macd_signal(self, close: pd.Series) -> Optional[str]:
    macd_df = ta.macd(close, fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
    if macd_df is None or macd_df.isna().all().all():
      return None

    macd_line = macd_df[f"MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"]
    signal_line = macd_df[f"MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"]

    if macd_line.isna().all() or signal_line.isna().all():
      return None

    prev_macd = macd_line.iloc[-2]
    last_macd = macd_line.iloc[-1]
    prev_signal = signal_line.iloc[-2]
    last_signal = signal_line.iloc[-1]

    if prev_macd <= prev_signal and last_macd > last_signal:
      return "BUY"
    elif prev_macd >= prev_signal and last_macd < last_signal:
      return "SELL"
    return None

  def _check_stop_take_profit(self, current_price: float):
    sl_triggered = False
    tp_triggered = False

    if self.position == "long":
      if current_price <= self.entry_price * (1 - self.stop_loss_pct):
        sl_triggered = True
      elif current_price >= self.entry_price * (1 + self.take_profit_pct):
        tp_triggered = True
    elif self.position == "short":
      if current_price >= self.entry_price * (1 + self.stop_loss_pct):
        sl_triggered = True
      elif current_price <= self.entry_price * (1 - self.take_profit_pct):
        tp_triggered = True

    return sl_triggered, tp_triggered

  async def _open_position(self, symbol: str, signal: str, price: float) -> bool:
    side = "buy" if signal == "BUY" else "sell"
    try:
      # Абстрактный вызов метода размещения ордера (предполагается реализация в базовом классе или пайплайне)
      order_result = await self.place_order(symbol=symbol, side=side, price=price)
      if order_result and order_result.get("success"):
        self.position = "long" if signal == "BUY" else "short"
        self.entry_price = price
        self.logger.info(f"[{self.strategy_name}/{symbol}] Открыта позиция {self.position} по цене {price}")
        return True
      else:
        self.logger.error(f"[{self.strategy_name}/{symbol}] Ошибка размещения ордера: {order_result}")
        return False
    except Exception as e:
      self.logger.error(f"[{self.strategy_name}/{symbol}] Exception при размещении ордера: {e}")
      return False

  async def _close_position(self, symbol: str, price: float, reason: str):
    side = "sell" if self.position == "long" else "buy"
    try:
      order_result = await self.place_order(symbol=symbol, side=side, price=price)
      if order_result and order_result.get("success"):
        self.logger.info(
          f"[{self.strategy_name}/{symbol}] Закрыта позиция {self.position} по цене {price}, причина: {reason}")
        self.position = None
        self.entry_price = None
      else:
        self.logger.error(f"[{self.strategy_name}/{symbol}] Ошибка закрытия позиции: {order_result}")
    except Exception as e:
      self.logger.error(f"[{self.strategy_name}/{symbol}] Exception при закрытии позиции: {e}")

  async def place_order(self, symbol: str, side: str, price: float) -> Dict[str, Any]:
    """
    Заглушка метода размещения ордера.
    Замените на реальную интеграцию с API биржи или вашим пайплайном.
    """
    # Пример:
    # result = await your_exchange_api.place_order(symbol=symbol, side=side, price=price, ...)
    # return result
    self.logger.debug(f"[{self.strategy_name}/{symbol}] place_order called: side={side}, price={price}")
    return {"success": True}