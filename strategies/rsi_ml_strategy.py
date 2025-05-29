import pandas as pd
import pandas_ta as ta  # Для RSI и других индикаторов, если нужно
from typing import Dict, Any, Optional

from strategies.base_strategy import BaseStrategy
from ml_models.lorentzian_classifier import LorentzianClassifier  # Наша ML модель
from config import RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD  # Параметры из config.py


# Предполагается, что ML модель уже обучена и готова к использованию.
# В реальном приложении ее нужно будет загрузить или обучить при инициализации.
# global_ml_model = LorentzianClassifier() # Можно инициализировать здесь или передавать в конструктор
# if not global_ml_model.is_fitted:
#    logger.warning("ML модель для RSI_ML_Strategy не обучена! Предсказания будут невозможны.")
#    # Здесь может быть логика загрузки обученной модели


class RsiMlStrategy(BaseStrategy):
  def __init__(self, ml_model: LorentzianClassifier, params: Optional[Dict[str, Any]] = None):
    super().__init__("RSI_ML_Strategy", params)
    self.rsi_period = self.params.get("rsi_period", RSI_PERIOD)
    self.rsi_overbought = self.params.get("rsi_overbought", RSI_OVERBOUGHT)
    self.rsi_oversold = self.params.get("rsi_oversold", RSI_OVERSOLD)
    self.ml_model = ml_model

    if not self.ml_model or not self.ml_model.is_fitted:
      self.logger.error("ML модель не обучена. Предсказания невозможны.")

  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if data.empty or 'close' not in data.columns or len(data) < self.rsi_period + 14:
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Недостаточно данных для анализа.")
      return None

    if data['rsi'].isna().all() or data['atr'].isna().all():
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Недостаточно данных для RSI или ATR.")
      return None

    latest_data_point = data.iloc[-1:]
    current_rsi = latest_data_point['rsi'].iloc[-1]
    current_price = latest_data_point['close'].iloc[-1]
    current_atr = latest_data_point['atr'].iloc[-1]

    data['rsi'] = ta.rsi(data['close'], length=self.rsi_period)
    data['atr'] = ta.atr(data['high'], data['low'], data['close'], length=14)

    latest = data.iloc[-1]
    if pd.isna(latest['rsi']) or pd.isna(latest['atr']):
      self.logger.warning(f"[{self.strategy_name}/{symbol}] NaN в индикаторах.")
      return None

    current_price = latest['close']
    current_rsi = latest['rsi']
    current_atr = latest['atr']

    # Предсказание от ML
    signal = "HOLD"
    ml_signal = None
    if self.ml_model and self.ml_model.is_fitted:
      try:
        ml_pred = self.ml_model.predict(data.tail(1))
        if ml_pred is not None and len(ml_pred) > 0:
          ml_signal = ml_pred[0]
          if ml_signal == 1:
            signal = "BUY"
          elif ml_signal == 2:
            signal = "SELL"
      except Exception as e:
        self.logger.error(f"[{self.strategy_name}/{symbol}] Ошибка ML предсказания: {e}", exc_info=True)

    # Fallback на RSI
    if signal == "HOLD":
      if current_rsi < self.rsi_oversold:
        signal = "BUY"
      elif current_rsi > self.rsi_overbought:
        signal = "SELL"

    if signal != "HOLD":
      stop_loss_value = current_price - 2 * current_atr if signal == "BUY" else current_price + 2 * current_atr
      take_profit_value = current_price + 3 * current_atr if signal == "BUY" else current_price - 3 * current_atr
      self.logger.info(
        f"[{self.strategy_name}/{symbol}] Сигнал: {signal}, Цена: {current_price}, SL: {stop_loss_value}, TP: {take_profit_value}")
      return {
        "signal": signal,
        "price": current_price,
        "strategy_name": self.strategy_name,
        "stop_loss": round(stop_loss_value, 4),
        "take_profit": round(take_profit_value, 4),
        "confidence": 1.0 if ml_signal else 0.5  # пример
      }

    return None

# Пример использования:
# async def main_test_rsi_ml_strategy():
#     from core.data_fetcher import DataFetcher
#     from core.bybit_connector import BybitConnector
#     from logger_setup import setup_logging
#     setup_logging("DEBUG")
#
#     # 0. Инициализация ML модели и ее обучение (здесь заглушка)
#     ml_model_instance = LorentzianClassifier(n_neighbors=3)
#     # ... код для загрузки данных и обучения ml_model_instance ...
#     # Для теста создадим "обученную" модель-пустышку
#     class DummyFittedModel(LorentzianClassifier):
#         def __init__(self):
#             super().__init__()
#             self.is_fitted = True # Говорим, что она "обучена"
#         def predict(self, X_df_new: pd.DataFrame) -> Optional[np.ndarray]:
#             if 'rsi' in X_df_new.columns:
#                 rsi_val = X_df_new['rsi'].iloc[-1]
#                 if rsi_val < 20: return np.array([1]) # Buy
#                 if rsi_val > 80: return np.array([2]) # Sell
#             return np.array([0]) # Hold
#
#     ml_model_instance = DummyFittedModel()
#
#     strategy = RsiMlStrategy(ml_model=ml_model_instance)
#
#     # 1. Получение данных (пример)
#     connector = BybitConnector()
#     await connector.init_session()
#     fetcher = DataFetcher(connector)
#     symbol = "BTCUSDT" # Пример символа
#     historical_data_df = await fetcher.get_historical_data(symbol, timeframe='5m', limit=100)
#     await connector.close_session()

#     if not historical_data_df.empty:
#         # Добавим High, Low если их нет, для ATR (pandas_ta может их требовать)
#         if 'high' not in historical_data_df.columns: historical_data_df['high'] = historical_data_df['close']
#         if 'low' not in historical_data_df.columns: historical_data_df['low'] = historical_data_df['close']
#
#         signal_result = await strategy.generate_signals(symbol, historical_data_df)
#         if signal_result:
#             logger.info(f"Итоговый сигнал от RSI_ML_Strategy для {symbol}: {signal_result}")
#         else:
#             logger.info(f"Нет сигнала от RSI_ML_Strategy для {symbol}.")
#     else:
#         logger.warning(f"Не удалось получить данные для {symbol} для теста стратегии.")

# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main_test_rsi_ml_strategy())