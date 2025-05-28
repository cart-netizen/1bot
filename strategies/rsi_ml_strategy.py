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
    self.ml_model = ml_model  # Передаем экземпляр ML модели

    if not self.ml_model or not self.ml_model.is_fitted:
      self.logger.error(
        "Предоставленная ML модель не обучена или отсутствует! Стратегия не сможет генерировать ML-сигналы.")
      # Можно установить флаг, чтобы не использовать ML часть, или стратегия должна выдавать ошибку

    self.logger.info(f"RSI_ML_Strategy использует RSI_period={self.rsi_period}, "
                     f"Overbought={self.rsi_overbought}, Oversold={self.rsi_oversold}")

  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Генерирует сигналы на основе RSI и предсказаний ML модели.
    data: DataFrame с колонкой 'close' для расчета RSI и другими признаками для ML.
    """
    if data.empty or 'close' not in data.columns:
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Нет данных или отсутствует колонка 'close'.")
      return None

    # 1. Расчет RSI
    # Используем pandas_ta для RSI. Polars-TA также можно использовать, если данные в формате Polars.
    # Убедимся, что данных достаточно для расчета RSI
    if len(data) < self.rsi_period:
      self.logger.warning(
        f"[{self.strategy_name}/{symbol}] Недостаточно данных для RSI (требуется {self.rsi_period}, есть {len(data)}).")
      return None

    data['rsi'] = ta.rsi(data['close'], length=self.rsi_period)
    if data['rsi'].isna().all():
      self.logger.warning(f"[{self.strategy_name}/{symbol}] Все значения RSI - NaN.")
      return None

    # Берем последние значения
    latest_data_point = data.iloc[-1:]  # DataFrame с одной последней строкой
    current_rsi = latest_data_point['rsi'].iloc[-1]
    current_price = latest_data_point['close'].iloc[-1]

    self.logger.debug(
      f"[{self.strategy_name}/{symbol}] Текущая цена: {current_price}, RSI({self.rsi_period}): {current_rsi:.2f}")

    # 2. Получение предсказания от ML модели
    ml_signal_prediction = None
    if self.ml_model and self.ml_model.is_fitted:
      # Убедимся, что `latest_data_point` содержит все признаки, необходимые для модели.
      # `_prepare_features` в LorentzianClassifier должен это проверить.
      try:
        # Модель может ожидать несколько признаков, включая 'rsi'
        # `predict` должен принимать DataFrame
        predictions = self.ml_model.predict(latest_data_point)
        if predictions is not None and len(predictions) > 0:
          ml_signal_prediction = predictions[0]  # Берем предсказание для последней точки
          self.logger.info(f"[{self.strategy_name}/{symbol}] Предсказание ML модели: {ml_signal_prediction}")
          # Предсказания: 0 - держать, 1 - купить, 2 - продать (согласно нашей заглушке)
      except Exception as e:
        self.logger.error(f"[{self.strategy_name}/{symbol}] Ошибка при получении предсказания от ML модели: {e}",
                          exc_info=True)
    else:
      self.logger.warning(
        f"[{self.strategy_name}/{symbol}] ML модель не обучена или недоступна, используется только RSI.")

    # 3. Комбинирование сигналов RSI и ML (логика может быть сложнее)
    # Пример простой логики:
    # - Если ML дает сильный сигнал, он может иметь приоритет.
    # - Если ML не уверен или нет сигнала, смотрим на RSI.

    signal = "HOLD"  # По умолчанию

    if ml_signal_prediction == 1:  # ML говорит "купить"
      signal = "BUY"
    elif ml_signal_prediction == 2:  # ML говорит "продать"
      signal = "SELL"
    else:  # ML не дал четкого сигнала или недоступен, используем RSI
      if current_rsi < self.rsi_oversold:
        signal = "BUY"
        self.logger.info(
          f"[{self.strategy_name}/{symbol}] RSI < {self.rsi_oversold} ({current_rsi:.2f}) -> Сигнал BUY (без ML)")
      elif current_rsi > self.rsi_overbought:
        signal = "SELL"
        self.logger.info(
          f"[{self.strategy_name}/{symbol}] RSI > {self.rsi_overbought} ({current_rsi:.2f}) -> Сигнал SELL (без ML)")

    if signal != "HOLD":
      self.logger.info(f"[{self.strategy_name}/{symbol}] Сгенерирован сигнал: {signal} по цене {current_price}")
      # Можно добавить stop_loss, take_profit на основе волатильности (ATR) или других правил
      # atr = ta.atr(data['high'], data['low'], data['close'], length=14)
      # current_atr = atr.iloc[-1] if not atr.empty and pd.notna(atr.iloc[-1]) else current_price * 0.02 # запасной вариант 2%
      # stop_loss_value = current_price - 2 * current_atr if signal == "BUY" else current_price + 2 * current_atr
      # take_profit_value = current_price + 3 * current_atr if signal == "BUY" else current_price - 3 * current_atr

      return {
        "signal": signal,
        "price": current_price,  # Может быть текущая рыночная или цена для лимитного ордера
        "strategy_name": self.strategy_name,
        # "stop_loss": round(stop_loss_value, 2),
        # "take_profit": round(take_profit_value, 2)
      }

    return None  # Нет сигнала или сигнал HOLD

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