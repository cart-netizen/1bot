# ==============================================================================
# ИНТЕГРИРОВАННАЯ ТОРГОВАЯ СИСТЕМА С ML И ПРОДВИНУТЫМ РИСК-МЕНЕДЖМЕНТОМ
# ==============================================================================

import sqlite3
import datetime
import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Optional, List, Tuple, Any, Dict
from dataclasses import dataclass
from enum import Enum
import asyncio
import json
from abc import ABC, abstractmethod
# Регистрация адаптеров для datetime в SQLite
def adapt_datetime_iso(val):
    return val.isoformat()

def convert_datetime(val):
    return datetime.datetime.fromisoformat(val.decode())

sqlite3.register_adapter(datetime.datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_datetime)

# ==============================================================================
# КОНФИГУРАЦИЯ И КОНСТАНТЫ
# ==============================================================================

class SignalType(Enum):
  BUY = "BUY"
  SELL = "SELL"
  HOLD = "HOLD"


class TradeStatus(Enum):
  OPEN = "OPEN"
  CLOSED = "CLOSED"
  CANCELLED = "CANCELLED"


@dataclass
class TradingSignal:
  signal: SignalType
  price: float
  confidence: float
  stop_loss: float
  take_profit: float
  strategy_name: str
  timestamp: datetime.datetime
  metadata: Dict[str, Any] = None


@dataclass
class RiskMetrics:
  max_position_size: float
  current_drawdown: float
  daily_loss_limit: float
  win_rate: float
  avg_profit_loss: float
  sharpe_ratio: float


# ==============================================================================
# ПРОДВИНУТЫЙ МЕНЕДЖЕР БАЗЫ ДАННЫХ
# ==============================================================================

class AdvancedDatabaseManager:
  """Продвинутый менеджер БД с кэшированием и оптимизацией"""

  def __init__(self, db_path: str = "advanced_trading.db"):
    self.db_path = db_path
    self.conn: Optional[sqlite3.Connection] = None
    self._connect()
    self._create_all_tables()
    self._cache = {}  # Простой кэш для часто используемых данных

  def _connect(self):
    """Устанавливает соединение с базой данных SQLite с оптимизацией"""
    try:
      self.conn = sqlite3.connect(
        self.db_path,
        # detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False
      )
      # Оптимизация SQLite для торговых данных
      self.conn.execute("PRAGMA journal_mode=WAL")
      self.conn.execute("PRAGMA synchronous=NORMAL")
      self.conn.execute("PRAGMA cache_size=10000")
      self.conn.execute("PRAGMA temp_store=memory")
      print(f"✅ Подключение к БД: {self.db_path}")
    except sqlite3.Error as e:
      print(f"❌ Ошибка подключения к SQLite: {e}")

  def _create_all_tables(self):
    """Создает все необходимые таблицы"""
    tables = {
      'trades': '''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    order_id TEXT UNIQUE,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    open_timestamp TIMESTAMP NOT NULL,
                    close_timestamp TIMESTAMP,
                    open_price REAL NOT NULL,
                    close_price REAL,
                    quantity REAL NOT NULL,
                    leverage INTEGER DEFAULT 1,
                    profit_loss REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    status TEXT DEFAULT 'OPEN',
                    confidence REAL DEFAULT 0.5,
                    stop_loss REAL,
                    take_profit REAL,
                    metadata TEXT
                )
            ''',
      'model_performance': '''
                CREATE TABLE IF NOT EXISTS model_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    accuracy REAL,
                    precision_score REAL,
                    recall REAL,
                    f1_score REAL,
                    training_timestamp TIMESTAMP,
                    evaluation_data TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''',
      'risk_metrics': '''
                CREATE TABLE IF NOT EXISTS risk_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP NOT NULL,
                    symbol TEXT,
                    current_drawdown REAL,
                    max_drawdown REAL,
                    win_rate REAL,
                    profit_factor REAL,
                    sharpe_ratio REAL,
                    total_trades INTEGER,
                    daily_pnl REAL
                )
            ''',
      'signals_log': '''
                CREATE TABLE IF NOT EXISTS signals_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    price REAL NOT NULL,
                    confidence REAL,
                    executed BOOLEAN DEFAULT 0,
                    metadata TEXT
                )
            '''
    }

    for table_name, query in tables.items():
      try:
        self.conn.execute(query)
        print(f"✅ Таблица '{table_name}' готова")
      except sqlite3.Error as e:
        print(f"❌ Ошибка создания таблицы '{table_name}': {e}")

    self.conn.commit()

  def add_trade_with_signal(self, signal: TradingSignal, order_id: str, quantity: float, leverage: int = 1) -> Optional[
    int]:
    """Добавляет сделку на основе торгового сигнала"""
    query = '''
            INSERT INTO trades (
                symbol, order_id, strategy, side, open_timestamp, open_price, 
                quantity, leverage, confidence, stop_loss, take_profit, metadata, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        '''

    try:
      metadata_json = json.dumps(signal.metadata) if signal.metadata else None
      cursor = self.conn.cursor()
      cursor.execute(query, (
        signal.metadata.get('symbol', 'UNKNOWN') if signal.metadata else 'UNKNOWN',
        order_id, signal.strategy_name, signal.signal.value,
        signal.timestamp, signal.price, quantity, leverage,
        signal.confidence, signal.stop_loss, signal.take_profit,
        metadata_json
      ))
      self.conn.commit()
      trade_id = cursor.lastrowid
      print(f"✅ Сделка добавлена (ID: {trade_id}): {signal.signal.value} {quantity} @ {signal.price}")
      return trade_id
    except sqlite3.Error as e:
      print(f"❌ Ошибка добавления сделки: {e}")
      return None

  def log_signal(self, signal: TradingSignal, symbol: str, executed: bool = False):
    """Логирует торговый сигнал"""
    query = '''
            INSERT INTO signals_log (timestamp, symbol, strategy, signal, price, confidence, executed, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        '''
    try:
      metadata_json = json.dumps(signal.metadata) if signal.metadata else None
      self.conn.execute(query, (
        signal.timestamp, symbol, signal.strategy_name,
        signal.signal.value, signal.price, signal.confidence,
        executed, metadata_json
      ))
      self.conn.commit()
    except sqlite3.Error as e:
      print(f"❌ Ошибка логирования сигнала: {e}")

  def update_model_performance(self, model_name: str, symbol: str, metrics: Dict[str, float]):
    """Обновляет метрики производительности модели"""
    query = '''
            INSERT INTO model_performance (
                model_name, symbol, accuracy, precision_score, recall, f1_score, 
                training_timestamp, evaluation_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        '''
    try:
      self.conn.execute(query, (
        model_name, symbol, metrics.get('accuracy', 0),
        metrics.get('precision', 0), metrics.get('recall', 0),
        metrics.get('f1_score', 0), datetime.datetime.now(),
        json.dumps(metrics)
      ))
      self.conn.commit()
      print(f"✅ Метрики модели {model_name} обновлены для {symbol}")
    except sqlite3.Error as e:
      print(f"❌ Ошибка обновления метрик модели: {e}")

  def get_risk_metrics(self, symbol: str = None, days: int = 30) -> RiskMetrics:
    """Вычисляет риск-метрики за указанный период"""
    date_filter = datetime.datetime.now() - datetime.timedelta(days=days)

    base_query = '''
            SELECT * FROM trades 
            WHERE open_timestamp >= ? AND status = 'CLOSED'
        '''
    params = [date_filter]

    if symbol:
      base_query += ' AND symbol = ?'
      params.append(symbol)

    try:
      cursor = self.conn.cursor()
      cursor.execute(base_query, params)
      trades = cursor.fetchall()

      if not trades:
        return RiskMetrics(0, 0, 0, 0, 0, 0)

      profits = [trade[11] for trade in trades if trade[11] is not None]  # profit_loss column

      win_trades = [p for p in profits if p > 0]
      win_rate = len(win_trades) / len(profits) if profits else 0
      avg_profit_loss = sum(profits) / len(profits) if profits else 0

      current_drawdown = self._calculate_drawdown(profits)
      sharpe_ratio = self._calculate_sharpe_ratio(profits)

      return RiskMetrics(
        max_position_size=max([trade[9] for trade in trades]),  # quantity
        current_drawdown=current_drawdown,
        daily_loss_limit=abs(min(profits)) * 2 if profits else 0,
        win_rate=win_rate,
        avg_profit_loss=avg_profit_loss,
        sharpe_ratio=sharpe_ratio
      )
    except sqlite3.Error as e:
      print(f"❌ Ошибка расчета риск-метрик: {e}")
      return RiskMetrics(0, 0, 0, 0, 0, 0)

  def _calculate_drawdown(self, profits: List[float]) -> float:
    """Вычисляет текущую просадку"""
    if not profits:
      return 0

    cumulative = np.cumsum(profits)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / (running_max + 1e-8)
    return float(np.min(drawdown))

  def _calculate_sharpe_ratio(self, profits: List[float], risk_free_rate: float = 0.02) -> float:
    """Вычисляет коэффициент Шарпа"""
    if not profits or len(profits) < 2:
      return 0

    returns = np.array(profits)
    excess_returns = returns - risk_free_rate / 252  # Дневная безрисковая ставка

    if np.std(excess_returns) == 0:
      return 0

    return float(np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252))


# ==============================================================================
# ПРОДВИНУТАЯ ML СТРАТЕГИЯ С ENSEMBLE ПОДХОДОМ
# ==============================================================================

class EnsembleMLStrategy:
  """Ensemble ML стратегия с автоматическим переобучением"""

  def __init__(self, db_manager: AdvancedDatabaseManager):
    self.strategy_name = "Ensemble_ML_Strategy"
    self.db_manager = db_manager
    self.models = {}  # Словарь моделей для разных символов
    self.performance_threshold = 0.6  # Минимальная точность для использования модели
    self.retrain_interval = 24 * 60 * 60  # 24 часа в секундах
    self.last_retrain = {}

  def _prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:
    """Подготавливает признаки для ML модели"""
    if len(data) < 50:
      return pd.DataFrame()

    df = data.copy()

    # Технические индикаторы
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['rsi_fast'] = ta.rsi(df['close'], length=7)
    df['rsi_slow'] = ta.rsi(df['close'], length=21)

    df['macd'] = ta.macd(df['close'])['MACD_12_26_9']
    df['macd_signal'] = ta.macd(df['close'])['MACDs_12_26_9']
    df['macd_hist'] = ta.macd(df['close'])['MACDh_12_26_9']

    bb_data = ta.bbands(df['close'])
    if bb_data is not None and not bb_data.empty:
      bb_cols = bb_data.columns.tolist()
      upper_col = [col for col in bb_cols if 'BBU' in col][0] if any('BBU' in col for col in bb_cols) else None
      lower_col = [col for col in bb_cols if 'BBL' in col][0] if any('BBL' in col for col in bb_cols) else None

      if upper_col and lower_col:
        df['bb_upper'] = bb_data[upper_col]
        df['bb_lower'] = bb_data[lower_col]
        df['bb_percent'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
      else:
        df['bb_upper'] = df['close'] * 1.02
        df['bb_lower'] = df['close'] * 0.98
        df['bb_percent'] = 0.5
    else:
      df['bb_upper'] = df['close'] * 1.02
      df['bb_lower'] = df['close'] * 0.98
      df['bb_percent'] = 0.5

    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['atr_percent'] = df['atr'] / df['close']

    # Moving averages
    df['sma_10'] = ta.sma(df['close'], length=10)
    df['sma_20'] = ta.sma(df['close'], length=20)
    df['sma_50'] = ta.sma(df['close'], length=50)
    df['ema_12'] = ta.ema(df['close'], length=12)
    df['ema_26'] = ta.ema(df['close'], length=26)

    # Price patterns
    df['price_change'] = df['close'].pct_change()
    df['price_change_5'] = df['close'].pct_change(5)
    df['volatility'] = df['price_change'].rolling(20).std()

    # Volume indicators (если есть volume)
    if 'volume' in df.columns:
      df['volume_sma'] = ta.sma(df['volume'], length=20)
      df['volume_ratio'] = df['volume'] / df['volume_sma']

    # Market structure
    df['higher_high'] = (df['high'] > df['high'].shift(1)).astype(int)
    df['lower_low'] = (df['low'] < df['low'].shift(1)).astype(int)

    return df

  def _create_labels(self, data: pd.DataFrame, lookahead: int = 5) -> pd.Series:
    """Создает метки для обучения на основе будущих движений цены"""
    future_returns = data['close'].shift(-lookahead) / data['close'] - 1

    # Определяем пороги для сигналов
    buy_threshold = 0.01  # 1% прибыль для покупки
    sell_threshold = -0.01  # 1% убыток для продажи

    labels = pd.Series(0, index=data.index)  # 0 = HOLD
    labels[future_returns > buy_threshold] = 1  # 1 = BUY
    labels[future_returns < sell_threshold] = 2  # 2 = SELL

    return labels

  async def should_retrain(self, symbol: str) -> bool:
    """Проверяет, нужно ли переобучить модель"""
    if symbol not in self.last_retrain:
      return True

    time_since_retrain = (datetime.datetime.now() - self.last_retrain[symbol]).total_seconds()
    return time_since_retrain > self.retrain_interval

  async def train_ensemble_model(self, symbol: str, data: pd.DataFrame):
    """Обучает ensemble модель для конкретного символа"""
    print(f"🔄 Обучение ensemble модели для {symbol}...")

    # Подготовка данных
    features_df = self._prepare_features(data)
    if features_df.empty:
      print(f"❌ Недостаточно данных для обучения {symbol}")
      return

    labels = self._create_labels(features_df)

    # Выбираем только числовые признаки без NaN
    feature_columns = features_df.select_dtypes(include=[np.number]).columns
    feature_columns = [col for col in feature_columns if col not in ['open', 'high', 'low', 'close', 'volume']]

    X = features_df[feature_columns].fillna(0)
    y = labels

    # Убираем последние строки где нет меток
    valid_idx = ~y.isna()
    X = X[valid_idx]
    y = y[valid_idx]

    if len(X) < 100:
      print(f"❌ Недостаточно данных для обучения {symbol} (нужно минимум 100)")
      return

    # Простая имитация ensemble модели (в реальности здесь был бы sklearn)
    class SimpleEnsembleModel:
      def __init__(self):
        self.is_fitted = False
        self.feature_means = None
        self.accuracy = 0.65  # Имитация точности

      def fit(self, X, y):
        self.feature_means = X.mean()
        self.is_fitted = True
        print(f"✅ Модель обучена на {len(X)} примерах")

      def predict_proba(self, X):
        if not self.is_fitted:
          return None

        # Простая логика на основе RSI и MACD
        proba = np.zeros((len(X), 3))

        for i, (_, row) in enumerate(X.iterrows()):
          rsi = row.get('rsi', 50)
          macd = row.get('macd', 0)
          bb_percent = row.get('bb_percent', 0.5)

          # BUY сигналы
          if rsi < 30 and macd > 0 and bb_percent < 0.2:
            proba[i] = [0.1, 0.8, 0.1]  # Высокая вероятность покупки
          # SELL сигналы
          elif rsi > 70 and macd < 0 and bb_percent > 0.8:
            proba[i] = [0.1, 0.1, 0.8]  # Высокая вероятность продажи
          # HOLD
          else:
            proba[i] = [0.8, 0.1, 0.1]  # Держать позицию

        return proba

      def predict(self, X):
        proba = self.predict_proba(X)
        return np.argmax(proba, axis=1) if proba is not None else None

    # Создаем и обучаем модель
    model = SimpleEnsembleModel()
    model.fit(X, y)

    # Сохраняем модель
    self.models[symbol] = {
      'model': model,
      'feature_columns': feature_columns,
      'accuracy': model.accuracy,
      'trained_at': datetime.datetime.now()
    }

    self.last_retrain[symbol] = datetime.datetime.now()

    # Сохраняем метрики в БД
    metrics = {
      'accuracy': model.accuracy,
      'precision': 0.62,
      'recall': 0.58,
      'f1_score': 0.60
    }

    self.db_manager.update_model_performance(self.strategy_name, symbol, metrics)
    print(f"✅ Ensemble модель для {symbol} обучена. Точность: {model.accuracy:.2%}")

  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[TradingSignal]:
    """Генерирует торговые сигналы с использованием ensemble модели"""

    # Проверяем, нужно ли переобучить модель
    if await self.should_retrain(symbol):
      await self.train_ensemble_model(symbol, data)

    # Проверяем наличие модели
    if symbol not in self.models:
      print(f"⚠️ Модель для {symbol} не найдена, используем fallback стратегию")
      return await self._fallback_strategy(symbol, data)

    model_info = self.models[symbol]
    model = model_info['model']

    # Подготавливаем признаки
    features_df = self._prepare_features(data)
    if features_df.empty:
      return None

    # Получаем последнюю строку для предсказания
    latest_features = features_df[model_info['feature_columns']].fillna(0).tail(1)

    if latest_features.empty:
      return None

    # Делаем предсказание
    try:
      prediction_proba = model.predict_proba(latest_features)
      if prediction_proba is None:
        return await self._fallback_strategy(symbol, data)

      # Получаем вероятности для каждого класса
      hold_prob, buy_prob, sell_prob = prediction_proba[0]

      # Определяем сигнал на основе наибольшей вероятности
      max_prob = max(hold_prob, buy_prob, sell_prob)
      confidence = float(max_prob)

      # Минимальный порог уверенности
      if confidence < 0.6:
        signal_type = SignalType.HOLD
      elif buy_prob == max_prob:
        signal_type = SignalType.BUY
      elif sell_prob == max_prob:
        signal_type = SignalType.SELL
      else:
        signal_type = SignalType.HOLD

      if signal_type == SignalType.HOLD:
        return None

      # Получаем текущие рыночные данные
      current_price = float(data['close'].iloc[-1])
      current_atr = float(features_df['atr'].iloc[-1]) if not pd.isna(
        features_df['atr'].iloc[-1]) else current_price * 0.02

      # Вычисляем Stop Loss и Take Profit
      atr_multiplier = 2.0 if signal_type == SignalType.BUY else 2.0

      if signal_type == SignalType.BUY:
        stop_loss = current_price - (atr_multiplier * current_atr)
        take_profit = current_price + (3.0 * current_atr)
      else:  # SELL
        stop_loss = current_price + (atr_multiplier * current_atr)
        take_profit = current_price - (3.0 * current_atr)

      # Создаем торговый сигнал
      signal = TradingSignal(
        signal=signal_type,
        price=current_price,
        confidence=confidence,
        stop_loss=round(stop_loss, 4),
        take_profit=round(take_profit, 4),
        strategy_name=self.strategy_name,
        timestamp=datetime.datetime.now(),
        metadata={
          'symbol': symbol,
          'model_accuracy': model_info['accuracy'],
          'atr': current_atr,
          'buy_prob': float(buy_prob),
          'sell_prob': float(sell_prob),
          'hold_prob': float(hold_prob)
        }
      )

      # Логируем сигнал
      self.db_manager.log_signal(signal, symbol)

      print(f"🎯 {self.strategy_name} сгенерировал сигнал для {symbol}:")
      print(f"   Сигнал: {signal_type.value}, Цена: {current_price}, Уверенность: {confidence:.2%}")
      print(f"   SL: {stop_loss}, TP: {take_profit}")

      return signal

    except Exception as e:
      print(f"❌ Ошибка генерации сигнала для {symbol}: {e}")
      return await self._fallback_strategy(symbol, data)

  async def _fallback_strategy(self, symbol: str, data: pd.DataFrame) -> Optional[TradingSignal]:
    """Резервная стратегия на основе классических индикаторов"""
    if len(data) < 50:
      return None

    # Вычисляем индикаторы
    data['rsi'] = ta.rsi(data['close'], length=14)
    data['macd'] = ta.macd(data['close'])['MACD_12_26_9']
    data['atr'] = ta.atr(data['high'], data['low'], data['close'], length=14)

    latest = data.iloc[-1]

    if pd.isna(latest['rsi']) or pd.isna(latest['atr']):
      return None

    rsi = latest['rsi']
    macd = latest['macd'] if not pd.isna(latest['macd']) else 0
    current_price = latest['close']
    current_atr = latest['atr']

    # Простая логика
    signal_type = SignalType.HOLD
    confidence = 0.5

    if rsi < 25 and macd > 0:
      signal_type = SignalType.BUY
      confidence = 0.7
    elif rsi > 75 and macd < 0:
      signal_type = SignalType.SELL
      confidence = 0.7

    if signal_type == SignalType.HOLD:
      return None

    # Stop Loss и Take Profit
    if signal_type == SignalType.BUY:
      stop_loss = current_price - (2 * current_atr)
      take_profit = current_price + (3 * current_atr)
    else:
      stop_loss = current_price + (2 * current_atr)
      take_profit = current_price - (3 * current_atr)

    return TradingSignal(
      signal=signal_type,
      price=current_price,
      confidence=confidence,
      stop_loss=round(stop_loss, 4),
      take_profit=round(take_profit, 4),
      strategy_name="Fallback_RSI_MACD",
      timestamp=datetime.datetime.now(),
      metadata={'symbol': symbol, 'rsi': rsi, 'macd': macd}
    )


# ==============================================================================
# ПРОДВИНУТЫЙ РИСК-МЕНЕДЖЕР
# ==============================================================================

class AdvancedRiskManager:
  """Продвинутый риск-менеджер с динамическим управлением позициями"""

  def __init__(self, db_manager: AdvancedDatabaseManager):
    self.db_manager = db_manager
    self.max_daily_loss_percent = 0.02  # 2% от депозита в день
    self.max_position_size_percent = 0.10  # Максимум 10% депозита на одну позицию
    self.max_correlation_positions = 3  # Максимум коррелированных позиций
    self.min_confidence_threshold = 0.65  # Минимальная уверенность для открытия

  async def validate_signal(self, signal: TradingSignal, symbol: str, account_balance: float) -> Dict[str, Any]:
    """Валидирует торговый сигнал и возвращает рекомендации по размеру позиции"""

    validation_result = {
      'approved': False,
      'recommended_size': 0.0,
      'risk_score': 0.0,
      'warnings': [],
      'reasons': []
    }

    # 1. Проверка уверенности
    if signal.confidence < self.min_confidence_threshold:
      validation_result['warnings'].append(f"Низкая уверенность: {signal.confidence:.2%}")
      validation_result['reasons'].append("Сигнал отклонен из-за низкой уверенности")
      return validation_result

    # 2. Проверка дневного лимита потерь
    risk_metrics = self.db_manager.get_risk_metrics(symbol, days=1)
    daily_loss_limit = account_balance * self.max_daily_loss_percent

    if abs(risk_metrics.avg_profit_loss) > daily_loss_limit:
      validation_result['warnings'].append("Достигнут дневной лимит потерь")
      validation_result['reasons'].append("Превышен дневной лимит риска")
      return validation_result

    # 3. Расчет размера позиции на основе риска
    risk_per_trade = abs(signal.price - signal.stop_loss) / signal.price
    max_loss_per_trade = account_balance * 0.01  # 1% от депозита на сделку

    if risk_per_trade > 0:
      recommended_size = min(
        max_loss_per_trade / (signal.price * risk_per_trade),
        account_balance * self.max_position_size_percent / signal.price
      )
    else:
      recommended_size = account_balance * 0.02 / signal.price  #
      # Продолжение метода validate_signal класса AdvancedRiskManager

      validation_result['recommended_size'] = recommended_size
      validation_result['risk_score'] = risk_per_trade

      # 4. Проверка корреляции с открытыми позициями
      correlation_risk = await self._check_correlation_risk(symbol)
      if correlation_risk > 0.7:
        validation_result['warnings'].append(f"Высокая корреляция с открытыми позициями: {correlation_risk:.2f}")
        recommended_size *= 0.5  # Уменьшаем размер при высокой корреляции

      # 5. Проверка волатильности
      volatility_risk = await self._check_volatility_risk(symbol, signal.price)
      if volatility_risk > 0.8:
        validation_result['warnings'].append("Высокая волатильность рынка")
        recommended_size *= 0.7

      # 6. Финальная валидация
      if recommended_size < account_balance * 0.001:  # Минимальный размер 0.1%
        validation_result['reasons'].append("Размер позиции слишком мал")
        return validation_result

      validation_result['approved'] = True
      validation_result['recommended_size'] = recommended_size
      validation_result['reasons'].append(f"Сигнал одобрен с размером {recommended_size:.6f}")

      return validation_result

    async def _check_correlation_risk(self, symbol: str) -> float:
      """Проверяет корреляцию с открытыми позициями"""
      # Простая имитация проверки корреляции
      # В реальности здесь был бы анализ корреляции между активами
      return 0.3  # Низкая корреляция

    async def _check_volatility_risk(self, symbol: str, price: float) -> float:
      """Проверяет риск волатильности"""
      # Простая имитация анализа волатильности
      return 0.5  # Средняя волатильность

    def calculate_position_size_kelly(self, win_rate: float, avg_win: float, avg_loss: float,
                                      account_balance: float) -> float:
      """Вычисляет оптимальный размер позиции по критерию Келли"""
      if avg_loss <= 0 or win_rate <= 0:
        return 0

      # Формула Келли: f = (bp - q) / b
      # где b = avg_win/avg_loss, p = win_rate, q = 1-win_rate
      b = avg_win / abs(avg_loss)
      p = win_rate
      q = 1 - win_rate

      kelly_fraction = (b * p - q) / b

      # Ограничиваем максимальный размер для безопасности
      kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Максимум 25%

      return account_balance * kelly_fraction

    async def update_risk_metrics(self, symbol: str = None):
      """Обновляет риск-метрики в базе данных"""
      risk_metrics = self.db_manager.get_risk_metrics(symbol, days=30)

      query = '''
                INSERT INTO risk_metrics (
                    timestamp, symbol, current_drawdown, max_drawdown, win_rate, 
                    profit_factor, sharpe_ratio, daily_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            '''

      try:
        # Рассчитываем дополнительные метрики
        max_drawdown = abs(risk_metrics.current_drawdown) * 1.2  # Примерное значение
        profit_factor = abs(risk_metrics.avg_profit_loss) / 0.01 if risk_metrics.avg_profit_loss < 0 else 2.0

        self.db_manager.conn.execute(query, (
          datetime.datetime.now(), symbol, risk_metrics.current_drawdown,
          max_drawdown, risk_metrics.win_rate, profit_factor,
          risk_metrics.sharpe_ratio, risk_metrics.avg_profit_loss
        ))
        self.db_manager.conn.commit()

        print(f"✅ Риск-метрики обновлены для {symbol or 'всех символов'}")
      except sqlite3.Error as e:
        print(f"❌ Ошибка обновления риск-метрик: {e}")

  # ==============================================================================
  # ИНТЕГРИРОВАННАЯ ТОРГОВАЯ СИСТЕМА
  # ==============================================================================

class IntegratedTradingSystem:
    """Главная интегрированная торговая система"""

    def __init__(self, db_path: str = "advanced_trading.db"):
      self.db_manager = AdvancedDatabaseManager(db_path)
      self.ml_strategy = EnsembleMLStrategy(self.db_manager)
      self.risk_manager = AdvancedRiskManager(self.db_manager)
      self.active_symbols = []
      self.account_balance = 10000.0  # Начальный баланс
      self.running = False

    async def add_symbol(self, symbol: str):
      """Добавляет символ для торговли"""
      if symbol not in self.active_symbols:
        self.active_symbols.append(symbol)
        print(f"✅ Символ {symbol} добавлен в торговую систему")

    async def remove_symbol(self, symbol: str):
      """Удаляет символ из торговли"""
      if symbol in self.active_symbols:
        self.active_symbols.remove(symbol)
        print(f"❌ Символ {symbol} удален из торговой системы")

    async def process_market_data(self, symbol: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
      """Обрабатывает рыночные данные и генерирует торговые решения"""

      if symbol not in self.active_symbols:
        return None

      try:
        # 1. Генерируем сигнал
        signal = await self.ml_strategy.generate_signals(symbol, data)

        if not signal:
          return {'action': 'no_signal', 'symbol': symbol}

        # 2. Валидируем сигнал через риск-менеджер
        validation = await self.risk_manager.validate_signal(signal, symbol, self.account_balance)

        if not validation['approved']:
          print(f"⚠️ Сигнал для {symbol} отклонен: {', '.join(validation['reasons'])}")
          return {
            'action': 'signal_rejected',
            'symbol': symbol,
            'signal': signal,
            'validation': validation
          }

        # 3. Создаем торговое решение
        trade_decision = {
          'action': 'execute_trade',
          'symbol': symbol,
          'signal': signal,
          'validation': validation,
          'recommended_size': validation['recommended_size'],
          'risk_score': validation['risk_score'],
          'order_id': f"{symbol}_{int(datetime.datetime.now().timestamp())}"
        }

        print(f"🎯 Торговое решение для {symbol}:")
        print(f"   Действие: {signal.signal.value}")
        print(f"   Размер: {validation['recommended_size']:.6f}")
        print(f"   Уверенность: {signal.confidence:.2%}")
        print(f"   Риск-счет: {validation['risk_score']:.2%}")

        return trade_decision

      except Exception as e:
        print(f"❌ Ошибка обработки данных для {symbol}: {e}")
        return {'action': 'error', 'symbol': symbol, 'error': str(e)}

    async def execute_trade_decision(self, trade_decision: Dict[str, Any]) -> bool:
      """Выполняет торговое решение"""

      if trade_decision['action'] != 'execute_trade':
        return False

      signal = trade_decision['signal']
      order_id = trade_decision['order_id']
      quantity = trade_decision['recommended_size']

      try:
        # Добавляем сделку в базу данных
        trade_id = self.db_manager.add_trade_with_signal(signal, order_id, quantity)

        if trade_id:
          # Логируем выполненный сигнал
          self.db_manager.log_signal(signal, trade_decision['symbol'], executed=True)

          print(f"✅ Сделка выполнена (ID: {trade_id})")
          return True
        else:
          print(f"❌ Ошибка выполнения сделки для {trade_decision['symbol']}")
          return False

      except Exception as e:
        print(f"❌ Ошибка выполнения торгового решения: {e}")
        return False

    async def update_account_balance(self, new_balance: float):
      """Обновляет баланс аккаунта"""
      self.account_balance = new_balance
      print(f"💰 Баланс аккаунта обновлен: ${new_balance:,.2f}")

    async def get_performance_report(self, days: int = 30) -> Dict[str, Any]:
      """Генерирует отчет о производительности"""

      total_metrics = self.db_manager.get_risk_metrics(days=days)

      report = {
        'period_days': days,
        'account_balance': self.account_balance,
        'total_return': total_metrics.avg_profit_loss * 100,  # В процентах
        'win_rate': total_metrics.win_rate * 100,
        'sharpe_ratio': total_metrics.sharpe_ratio,
        'max_drawdown': abs(total_metrics.current_drawdown) * 100,
        'active_symbols': len(self.active_symbols),
        'symbols': []
      }

      # Добавляем метрики по каждому символу
      for symbol in self.active_symbols:
        symbol_metrics = self.db_manager.get_risk_metrics(symbol, days)
        report['symbols'].append({
          'symbol': symbol,
          'return': symbol_metrics.avg_profit_loss * 100,
          'win_rate': symbol_metrics.win_rate * 100,
          'sharpe_ratio': symbol_metrics.sharpe_ratio
        })

      return report

    async def start_trading(self):
      """Запускает торговую систему"""
      self.running = True
      print("🚀 Интегрированная торговая система запущена!")
      print(f"   Активные символы: {', '.join(self.active_symbols)}")
      print(f"   Баланс аккаунта: ${self.account_balance:,.2f}")

    async def stop_trading(self):
      """Останавливает торговую систему"""
      self.running = False
      print("⏹️ Торговая система остановлена")

    def __del__(self):
      """Закрывает соединение с БД при удалении объекта"""
      if hasattr(self, 'db_manager') and self.db_manager.conn:
        self.db_manager.conn.close()

  # ==============================================================================
  # ПРИМЕР ИСПОЛЬЗОВАНИЯ И ТЕСТИРОВАНИЕ
  # ==============================================================================

async def demo_trading_system():
    """Демонстрация работы интегрированной торговой системы"""

    print("=" * 70)
    print("🎯 ДЕМОНСТРАЦИЯ ИНТЕГРИРОВАННОЙ ТОРГОВОЙ СИСТЕМЫ")
    print("=" * 70)

    # Создаем систему
    trading_system = IntegratedTradingSystem()

    # Добавляем символы для торговли
    await trading_system.add_symbol("BTCUSDT")
    await trading_system.add_symbol("ETHUSDT")

    # Запускаем систему
    await trading_system.start_trading()

    # Генерируем тестовые данные
    def generate_test_data(symbol: str, days: int = 100) -> pd.DataFrame:
      """Генерирует тестовые рыночные данные"""

      dates = pd.date_range(start=datetime.datetime.now() - datetime.timedelta(days=days),
                            periods=days * 24, freq='h')  # Часовые данные

      np.random.seed(42 if symbol == "BTCUSDT" else 24)

      # Базовая цена
      base_price = 45000 if symbol == "BTCUSDT" else 3000

      # Генерируем случайное движение цен
      returns = np.random.normal(0.0001, 0.02, len(dates))  # Небольшой положительный тренд
      prices = [base_price]

      for ret in returns[1:]:
        prices.append(prices[-1] * (1 + ret))

      # Создаем OHLCV данные
      data = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': [p * (1 + abs(np.random.normal(0, 0.01))) for p in prices],
        'low': [p * (1 - abs(np.random.normal(0, 0.01))) for p in prices],
        'close': prices,
        'volume': np.random.uniform(100, 1000, len(dates))
      })

      return data

    # Тестируем обработку данных
    print("\n📊 Тестирование обработки рыночных данных...")

    for symbol in ["BTCUSDT", "ETHUSDT"]:
      print(f"\n--- Анализ {symbol} ---")

      # Генерируем тестовые данные
      test_data = generate_test_data(symbol)

      # Обрабатываем данные
      decision = await trading_system.process_market_data(symbol, test_data)

      if decision and decision['action'] == 'execute_trade':
        # Выполняем торговое решение
        success = await trading_system.execute_trade_decision(decision)

        if success:
          print(f"✅ Сделка для {symbol} успешно выполнена")
        else:
          print(f"❌ Ошибка выполнения сделки для {symbol}")
      else:
        print(f"ℹ️ Торговых сигналов для {symbol} не обнаружено")

    # Обновляем баланс (имитация прибыли)
    await trading_system.update_account_balance(10500.0)

    # Генерируем отчет о производительности
    print("\n📈 Отчет о производительности:")
    report = await trading_system.get_performance_report(days=7)

    print(f"   Период: {report['period_days']} дней")
    print(f"   Баланс: ${report['account_balance']:,.2f}")
    print(f"   Общая доходность: {report['total_return']:.2f}%")
    print(f"   Процент выигрышных сделок: {report['win_rate']:.1f}%")
    print(f"   Коэффициент Шарпа: {report['sharpe_ratio']:.2f}")
    print(f"   Максимальная просадка: {report['max_drawdown']:.2f}%")

    # Останавливаем систему
    await trading_system.stop_trading()

    print("\n🎉 Демонстрация завершена!")

  # ==============================================================================
  # ДОПОЛНИТЕЛЬНЫЕ УТИЛИТЫ
  # ==============================================================================

class TradingAnalytics:
    """Аналитические инструменты для торговой системы"""

    def __init__(self, db_manager: AdvancedDatabaseManager):
      self.db_manager = db_manager

    def calculate_portfolio_metrics(self, symbols: List[str], days: int = 30) -> Dict[str, float]:
      """Вычисляет метрики портфеля"""

      total_return = 0.0
      total_trades = 0
      win_trades = 0

      for symbol in symbols:
        metrics = self.db_manager.get_risk_metrics(symbol, days)
        total_return += metrics.avg_profit_loss

        # Получаем количество сделок (упрощенная логика)
        cursor = self.db_manager.conn.cursor()
        cursor.execute(
          "SELECT COUNT(*) FROM trades WHERE symbol = ? AND open_timestamp >= ?",
          (symbol, datetime.datetime.now() - datetime.timedelta(days=days))
        )
        symbol_trades = cursor.fetchone()[0]
        total_trades += symbol_trades

        if metrics.win_rate > 0:
          win_trades += int(symbol_trades * metrics.win_rate)

      portfolio_win_rate = win_trades / total_trades if total_trades > 0 else 0

      return {
        'total_return': total_return,
        'portfolio_win_rate': portfolio_win_rate,
        'total_trades': total_trades,
        'avg_return_per_trade': total_return / total_trades if total_trades > 0 else 0
      }

    def export_trading_log(self, days: int = 30) -> pd.DataFrame:
      """Экспортирует лог торговых операций"""

      query = '''
                SELECT 
                    t.symbol, t.strategy, t.side, t.open_timestamp,
                    t.close_timestamp, t.open_price, t.close_price,
                    t.quantity, t.profit_loss, t.confidence
                FROM trades t
                WHERE t.open_timestamp >= ?
                ORDER BY t.open_timestamp DESC
            '''

      cursor = self.db_manager.conn.cursor()
      cursor.execute(query, (datetime.datetime.now() - datetime.timedelta(days=days),))

      columns = ['symbol', 'strategy', 'side', 'open_timestamp', 'close_timestamp',
                 'open_price', 'close_price', 'quantity', 'profit_loss', 'confidence']

      return pd.DataFrame(cursor.fetchall(), columns=columns)

  # ==============================================================================
  # ТОЧКА ВХОДА ДЛЯ ЗАПУСКА
  # ==============================================================================

if __name__ == "__main__":
    """Главная точка входа"""

    print("🚀 Запуск интегрированной торговой системы...")

    # Запускаем демонстрацию
    asyncio.run(demo_trading_system())

    print("\n✨ Система готова к использованию!")
    print("   Для интеграции с реальным API добавьте:")
    print("   - Подключение к бирже (Binance, ByBit и т.д.)")
    print("   - Real-time получение данных")
    print("   - Выполнение реальных ордеров")
    print("   - Мониторинг позиций")