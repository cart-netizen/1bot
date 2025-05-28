import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Dict, List, Optional, Tuple, Any
from sklearn.model_selection import train_test_split, TimeSeriesSplit, cross_val_score
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import asyncio

from ml_models.lorentzian_classifier import LorentzianClassifier, create_training_labels
from logger_setup import get_logger

logger = get_logger(__name__)


class ModelTrainer:
  """
  Класс для обучения и валидации ML моделей для торговых стратегий.
  """

  def __init__(self, data_path: Optional[str] = None):
    self.data_path = data_path
    self.models = {}
    self.training_history = []

  def prepare_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
    """
    Добавляет технические индикаторы к историческим данным.
    """
    if df.empty or not all(col in df.columns for col in ['open', 'high', 'low', 'close', 'volume']):
      logger.error("DataFrame должен содержать OHLCV колонки")
      return df

    logger.info("Расчет технических индикаторов...")

    # RSI
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['rsi_9'] = ta.rsi(df['close'], length=9)
    df['rsi_21'] = ta.rsi(df['close'], length=21)

    # MACD
    macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd_df is not None:
      df['macd'] = macd_df.iloc[:, 0]  # MACD line
      df['macd_signal'] = macd_df.iloc[:, 1]  # Signal line
      df['macd_histogram'] = macd_df.iloc[:, 2]  # Histogram

    # Moving Averages
    df['sma_20'] = ta.sma(df['close'], length=20)
    df['sma_50'] = ta.sma(df['close'], length=50)
    df['ema_12'] = ta.ema(df['close'], length=12)
    df['ema_26'] = ta.ema(df['close'], length=26)

    # Bollinger Bands
    bb_df = ta.bbands(df['close'], length=20, std=2)
    if bb_df is not None:
      df['bb_upper'] = bb_df.iloc[:, 0]
      df['bb_middle'] = bb_df.iloc[:, 1]
      df['bb_lower'] = bb_df.iloc[:, 2]
      df['bb_percent'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
      df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']

    # ATR (Average True Range)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

    # Stochastic
    stoch_df = ta.stoch(df['high'], df['low'], df['close'])
    if stoch_df is not None:
      df['stoch_k'] = stoch_df.iloc[:, 0]
      df['stoch_d'] = stoch_df.iloc[:, 1]

    # Williams %R
    df['williams_r'] = ta.willr(df['high'], df['low'], df['close'], length=14)

    # Volume indicators
    df['volume_sma'] = ta.sma(df['volume'], length=20)
    df['volume_ratio'] = df['volume'] / df['volume_sma']

    # Price-based features
    df['price_change'] = df['close'].pct_change()
    df['price_change_abs'] = df['price_change'].abs()
    df['high_low_pct'] = (df['high'] - df['low']) / df['close']
    df['open_close_pct'] = (df['close'] - df['open']) / df['open']

    # Volatility
    df['volatility'] = df['close'].rolling(window=20).std()
    df['volatility_ratio'] = df['volatility'] / df['volatility'].rolling(window=50).mean()

    logger.info(
      f"Добавлено {len([col for col in df.columns if col not in ['open', 'high', 'low', 'close', 'volume', 'timestamp']])} технических индикаторов")

    return df

  def create_features_and_labels(self,
                                 df: pd.DataFrame,
                                 future_bars: int = 5,
                                 profit_threshold: float = 0.015,
                                 loss_threshold: float = 0.01) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Создает признаки и метки для обучения модели.

    Args:
        df: DataFrame с OHLCV данными и индикаторами
        future_bars: Количество баров для анализа будущих движений
        profit_threshold: Минимальный порог прибыли для BUY сигнала
        loss_threshold: Минимальный порог убытка для SELL сигнала

    Returns:
        Tuple с признаками и метками
    """
    logger.info(f"Создание признаков и меток (future_bars={future_bars}, profit_threshold={profit_threshold})")

    # Добавляем технические индикаторы если их нет
    if 'rsi' not in df.columns:
      df = self.prepare_technical_indicators(df)

    # Создаем дополнительные признаки
    df = self._add_advanced_features(df)

    # Создаем метки
    labels = self._create_advanced_labels(df, future_bars, profit_threshold, loss_threshold)

    # Убираем строки с NaN
    feature_columns = [col for col in df.columns if col not in ['open', 'high', 'low', 'close', 'volume', 'timestamp']]
    features_df = df[feature_columns].copy()

    # Синхронизируем индексы
    valid_idx = features_df.dropna().index.intersection(labels.dropna().index)
    features_clean = features_df.loc[valid_idx]
    labels_clean = labels.loc[valid_idx]

    logger.info(f"Создано {len(features_clean)} обучающих примеров с {len(feature_columns)} признаками")
    logger.info(f"Распределение классов: {labels_clean.value_counts().to_dict()}")

    return features_clean, labels_clean

  def _add_advanced_features(self, df: pd.DataFrame) -> pd.DataFrame:
    """
    Добавляет продвинутые признаки для улучшения качества модели.
    """
    # Momentum features
    df['momentum_3'] = df['close'] / df['close'].shift(3) - 1
    df['momentum_5'] = df['close'] / df['close'].shift(5) - 1
    df['momentum_10'] = df['close'] / df['close'].shift(10) - 1

    # RSI дивергенция
    df['rsi_change'] = df['rsi'].diff()
    df['price_change_3'] = df['close'].pct_change(3)

    # Trend strength
    df['trend_strength'] = (df['close'] / df['sma_20'] - 1) if 'sma_20' in df.columns else 0

    # Volume-price trend
    if 'volume' in df.columns:
      df['vpt'] = (df['volume'] * df['close'].pct_change()).cumsum()
      df['vpt_sma'] = df['vpt'].rolling(window=10).mean()

    # Support/Resistance levels
    df['local_high'] = df['high'].rolling(window=10, center=True).max()
    df['local_low'] = df['low'].rolling(window=10, center=True).min()
    df['distance_to_high'] = (df['close'] - df['local_high']) / df['close']
    df['distance_to_low'] = (df['close'] - df['local_low']) / df['close']

    return df

  def _create_advanced_labels(self,
                              df: pd.DataFrame,
                              future_bars: int,
                              profit_threshold: float,
                              loss_threshold: float) -> pd.Series:
    """
    Создает улучшенные метки учитывающие не только направление движения,
    но и силу тренда и временные рамки.
    """
    labels = []

    for i in range(len(df)):
      if i + future_bars >= len(df):
        labels.append(0)  # HOLD для последних баров
        continue

      current_price = df.iloc[i]['close']
      future_prices = df.iloc[i + 1:i + future_bars + 1]['close']

      # Анализируем максимальную прибыль/убыток
      max_profit = (future_prices.max() - current_price) / current_price
      max_loss = (current_price - future_prices.min()) / current_price

      # Анализируем финальную цену
      final_price = future_prices.iloc[-1]
      final_return = (final_price - current_price) / current_price

      # Логика принятия решений
      if max_profit > profit_threshold and final_return > profit_threshold * 0.5:
        # Сильный BUY сигнал: высокий потенциал прибыли и положительный финальный результат
        labels.append(1)
      elif max_loss > loss_threshold and final_return < -loss_threshold * 0.5:
        # Сильный SELL сигнал: высокий риск потерь и отрицательный финальный результат
        labels.append(2)
      else:
        # HOLD: неопределенная ситуация
        labels.append(0)

    return pd.Series(labels, index=df.index)

  def train_lorentzian_model(self,
                             df: pd.DataFrame,
                             model_params: Optional[Dict] = None,
                             validation_method: str = 'time_series',
                             test_size: float = 0.2) -> Dict[str, Any]:
    """
    Обучает Lorentzian Classifier и проводит валидацию.

    Args:
        df: DataFrame с историческими данными
        model_params: Параметры модели
        validation_method: Метод валидации ('simple', 'time_series', 'cross_val')
        test_size: Размер тестовой выборки

    Returns:
        Словарь с результатами обучения и метриками
    """
    if model_params is None:
      model_params = {
        'k_neighbors': 8,
        'max_lookback': 2000,
        'feature_weights': {
          'rsi': 1.2,
          'macd': 1.1,
          'bb_percent': 1.0,
          'atr': 0.9,
          'volume_ratio': 0.8
        }
      }

    logger.info("Начало обучения Lorentzian модели...")

    # Подготавливаем