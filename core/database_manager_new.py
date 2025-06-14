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
import time
from abc import ABC, abstractmethod

from PyQt6.QtWidgets import QTableWidgetItem

# from core.trade_executor import TradeExecutor
from core.bybit_connector import BybitConnector
from ml_models.lorentzian_classifier import LorentzianClassifier
from strategies.base_strategy import BaseStrategy


#from data_fetcher import DataFetcher
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
  total_trades = 0
  winning_trades = 0
  losing_trades = 0
  win_rate = 0.0
  total_pnl = 0.0
  avg_win = 0.0
  avg_loss = 0.0
  profit_factor = 0.0

  # Добавляем недостающие атрибуты
  max_drawdown = 0.0
  current_drawdown = 0.0
  sharpe_ratio = 0.0
  volatility = 0.0
  max_consecutive_losses = 0
  max_consecutive_wins = 0
  risk_reward_ratio = 0.0
  recovery_factor = 0.0
  calmar_ratio = 0.0

  # Временные метрики
  daily_pnl = 0.0
  weekly_pnl = 0.0
  monthly_pnl = 0.0

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
    self.add_missing_columns()


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

  def add_missing_columns(self):
    """Добавить отсутствующие столбцы в существующие таблицы"""
    try:
      cursor = self.conn.cursor()

      # Проверяем существование столбца created_at в таблице trades
      cursor.execute("PRAGMA table_info(trades)")
      columns = [column[1] for column in cursor.fetchall()]

      if 'created_at' not in columns:
        cursor.execute("""
                ALTER TABLE trades 
                ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
        print("Добавлен столбец created_at в таблицу trades")

      self.conn.commit()

    except Exception as e:
      print(f"Ошибка при добавлении столбцов: {e}")


  def get_all_trades(self, limit: int = 50) -> List[Dict]:
    """Получить все сделки с лимитом"""
    try:
      cursor = self.conn.cursor()
      cursor.execute("""
              SELECT * FROM trades 
              ORDER BY created_at DESC 
              LIMIT ?
          """, (limit,))

      columns = [description[0] for description in cursor.description]
      trades = []

      for row in cursor.fetchall():
        trade_dict = dict(zip(columns, row))
        trades.append(trade_dict)

      return trades

    except Exception as e:
      print(f"Ошибка при получении сделок: {e}")
      return []

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

  def get_risk_metrics(self, symbol: str = None):
    """Получить риск-метрики для символа"""
    try:
      metrics = RiskMetrics()

      # Получаем сделки
      if symbol:
        trades = self.get_trades_for_symbol(symbol)
      else:
        trades = self.get_all_trades(limit=1000)

      if not trades:
        return metrics

      # Основные метрики
      metrics.total_trades = len(trades)
      profitable_trades = [t for t in trades if t.get('pnl', 0) > 0]
      losing_trades = [t for t in trades if t.get('pnl', 0) < 0]

      metrics.winning_trades = len(profitable_trades)
      metrics.losing_trades = len(losing_trades)

      if metrics.total_trades > 0:
        metrics.win_rate = metrics.winning_trades / metrics.total_trades

      # PnL метрики
      all_pnl = [t.get('pnl', 0) for t in trades]
      metrics.total_pnl = sum(all_pnl)

      if profitable_trades:
        metrics.avg_win = sum(t.get('pnl', 0) for t in profitable_trades) / len(profitable_trades)

      if losing_trades:
        metrics.avg_loss = sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades)

      # Profit Factor
      total_profit = sum(t.get('pnl', 0) for t in profitable_trades)
      total_loss = abs(sum(t.get('pnl', 0) for t in losing_trades))

      if total_loss > 0:
        metrics.profit_factor = total_profit / total_loss

      # Временные PnL
      metrics.daily_pnl = self._calculate_daily_pnl(trades)
      metrics.weekly_pnl = self._calculate_weekly_pnl(trades)
      metrics.monthly_pnl = self._calculate_monthly_pnl(trades)

      # Риск метрики
      metrics.max_drawdown = self._calculate_max_drawdown(all_pnl)
      metrics.sharpe_ratio = self._calculate_sharpe_ratio(all_pnl)
      metrics.volatility = self._calculate_volatility(all_pnl)

      # Дополнительные метрики
      metrics.max_consecutive_wins = self._calculate_max_consecutive_wins(trades)
      metrics.max_consecutive_losses = self._calculate_max_consecutive_losses(trades)

      if metrics.avg_loss != 0:
        metrics.risk_reward_ratio = abs(metrics.avg_win / metrics.avg_loss)

      return metrics

    except Exception as e:
      print(f"Ошибка при расчете риск-метрик: {e}")
      return RiskMetrics()

  def _calculate_daily_pnl(self, trades: list) -> float:
      """Рассчитать дневной PnL"""
      try:
        from datetime import datetime, timedelta

        today = datetime.now().date()
        daily_trades = []

        for trade in trades:
          # Попробуем извлечь дату из разных возможных полей
          trade_date = None

          if 'created_at' in trade and trade['created_at']:
            try:
              if isinstance(trade['created_at'], str):
                trade_date = datetime.strptime(trade['created_at'][:10], '%Y-%m-%d').date()
              else:
                trade_date = trade['created_at'].date()
            except:
              pass

          if trade_date and trade_date == today:
            daily_trades.append(trade)

        return sum(t.get('pnl', 0) for t in daily_trades)

      except Exception as e:
        print(f"Ошибка при расчете дневного PnL: {e}")
        return 0.0

  def _calculate_weekly_pnl(self, trades: list) -> float:
    """Рассчитать недельный PnL"""
    try:
      from datetime import datetime, timedelta

      today = datetime.now().date()
      week_ago = today - timedelta(days=7)
      weekly_trades = []

      for trade in trades:
        trade_date = None

        if 'created_at' in trade and trade['created_at']:
          try:
            if isinstance(trade['created_at'], str):
              trade_date = datetime.strptime(trade['created_at'][:10], '%Y-%m-%d').date()
            else:
              trade_date = trade['created_at'].date()
          except:
            pass

        if trade_date and week_ago <= trade_date <= today:
          weekly_trades.append(trade)

      return sum(t.get('pnl', 0) for t in weekly_trades)

    except Exception as e:
      print(f"Ошибка при расчете недельного PnL: {e}")
      return 0.0

  def _calculate_monthly_pnl(self, trades: list) -> float:
    """Рассчитать месячный PnL"""
    try:
      from datetime import datetime, timedelta

      today = datetime.now().date()
      month_ago = today - timedelta(days=30)
      monthly_trades = []

      for trade in trades:
        trade_date = None

        if 'created_at' in trade and trade['created_at']:
          try:
            if isinstance(trade['created_at'], str):
              trade_date = datetime.strptime(trade['created_at'][:10], '%Y-%m-%d').date()
            else:
              trade_date = trade['created_at'].date()
          except:
            pass

        if trade_date and month_ago <= trade_date <= today:
          monthly_trades.append(trade)

      return sum(t.get('pnl', 0) for t in monthly_trades)

    except Exception as e:
      print(f"Ошибка при расчете месячного PnL: {e}")
      return 0.0

  def _calculate_sharpe_ratio(self, pnl_series: list) -> float:
    """Рассчитать коэффициент Шарпа"""
    try:
      if len(pnl_series) < 2:
        return 0.0

      import statistics

      mean_return = statistics.mean(pnl_series)
      std_return = statistics.stdev(pnl_series)

      if std_return == 0:
        return 0.0

      return mean_return / std_return

    except Exception as e:
      print(f"Ошибка при расчете коэффициента Шарпа: {e}")
      return 0.0

  def _calculate_volatility(self, pnl_series: list) -> float:
    """Рассчитать волатильность"""
    try:
      if len(pnl_series) < 2:
        return 0.0

      import statistics
      return statistics.stdev(pnl_series)

    except Exception as e:
      print(f"Ошибка при расчете волатильности: {e}")
      return 0.0

  def _calculate_max_consecutive_wins(self, trades: list) -> int:
    """Рассчитать максимальное количество последовательных выигрышей"""
    try:
      max_wins = 0
      current_wins = 0

      for trade in trades:
        if trade.get('pnl', 0) > 0:
          current_wins += 1
          max_wins = max(max_wins, current_wins)
        else:
          current_wins = 0

      return max_wins

    except Exception as e:
      print(f"Ошибка при расчете максимальных последовательных выигрышей: {e}")
      return 0

  def _calculate_max_consecutive_losses(self, trades: list) -> int:
    """Рассчитать максимальное количество последовательных проигрышей"""
    try:
      max_losses = 0
      current_losses = 0

      for trade in trades:
        if trade.get('pnl', 0) < 0:
          current_losses += 1
          max_losses = max(max_losses, current_losses)
        else:
          current_losses = 0

      return max_losses

    except Exception as e:
      print(f"Ошибка при расчете максимальных последовательных проигрышей: {e}")
      return 0

  def _calculate_max_drawdown(self, pnl_series: list) -> float:
    """Вычислить максимальную просадку"""
    if not pnl_series:
      return 0.0

    try:
      cumulative_pnl = []
      running_total = 0

      for pnl in pnl_series:
        running_total += pnl
        cumulative_pnl.append(running_total)

      if not cumulative_pnl:
        return 0.0

      max_drawdown = 0.0
      peak = cumulative_pnl[0]

      for current_value in cumulative_pnl:
        if current_value > peak:
          peak = current_value

        if peak > 0:
          drawdown = (peak - current_value) / peak
          max_drawdown = max(max_drawdown, drawdown)

      return max_drawdown

    except Exception as e:
      print(f"Ошибка при расчете максимальной просадки: {e}")
      return 0.0

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

  def get_trades_for_symbol(self, symbol: str, limit: int = 100) -> List[Dict]:
    """Получить сделки для конкретного символа"""
    try:
      cursor = self.conn.cursor()

      # Проверяем структуру таблицы
      cursor.execute("PRAGMA table_info(trades)")
      columns_info = cursor.fetchall()
      column_names = [col[1] for col in columns_info]

      # Выбираем столбец для сортировки
      if 'created_at' in column_names:
        order_column = 'created_at'
      elif 'id' in column_names:
        order_column = 'id'
      else:
        order_column = 'rowid'

      cursor.execute(f"""
            SELECT * FROM trades 
            WHERE symbol = ?
            ORDER BY {order_column} DESC 
            LIMIT ?
        """, (symbol, limit))

      columns = [description[0] for description in cursor.description]
      trades = []

      for row in cursor.fetchall():
        trade_dict = dict(zip(columns, row))
        trades.append(trade_dict)

      return trades

    except Exception as e:
      print(f"Ошибка при получении сделок для символа {symbol}: {e}")
      return []

# ==============================================================================
# ПРОДВИНУТАЯ ML СТРАТЕГИЯ С ENSEMBLE ПОДХОДОМ
# ==============================================================================

class EnsembleMLStrategy:
  """Ensemble ML стратегия с автоматическим переобучением"""

  def __init__(self, db_manager: AdvancedDatabaseManager, ml_model: LorentzianClassifier = None):
    self.strategy_name = "Ensemble_ML_Strategy"
    self.db_manager = db_manager
    self.models = ml_model
    self.performance_threshold = 0.6  # Минимальная точность для использования модели
    self.retrain_interval = 24 * 60 * 60  # 24 часа в секундах
    self.last_retrain = {}
    self.models = {}

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



  async def generate_signals(self, symbol: str, data: pd.DataFrame) -> Optional[TradingSignal]:
    """Генерирует торговые сигналы с использованием ensemble модели"""

    if self.ml_model and self.ml_model.is_fitted:
      features_df = self._prepare_features(data)
      if features_df.empty:
        return None

      latest_features = features_df.tail(1)
      prediction_proba = self.ml_model.predict_proba(latest_features)

      if prediction_proba is None:
        return await self._fallback_strategy(symbol, data)

    # Проверяем, нужно ли переобучить модель
    if await self.should_retrain(symbol):
      await self.train_ensemble_model(symbol, data)

    # Проверяем наличие модели
    if symbol not in self.models:
      print(f"⚠️ Модель для {symbol} не найдена, используем fallback стратегию")
      return await self._fallback_strategy(symbol, data)

    model_info = self.models[symbol]
    model = model_info['model']

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

  def __init__(self, db_manager: AdvancedDatabaseManager, connector: BybitConnector = None):
    self.connector = connector
    self.db_manager = db_manager
    self.max_daily_loss_percent = 0.02  # 2% от депозита в день
    self.max_position_size_percent = 0.10  # Максимум 10% депозита на одну позицию
    self.max_correlation_positions = 3  # Максимум коррелированных позиций
    self.min_confidence_threshold = 0.65  # Минимальная уверенность для открытия
    self.correlation_threshold = 0.7  # Порог корреляции для уменьшения позиции
    self.volatility_threshold = 0.8  # Порог волатильности
    self.slippage_threshold = 0.001  # 0.1% допустимого скольжения

    # Кэш для хранения корреляционной матрицы
    self.correlation_cache = {
      'timestamp': None,
      'matrix': None,
      'valid_period': 3600  # Актуальность 1 час
    }

  async def calculate_correlation_matrix(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """Вычисляет корреляционную матрицу для активов на основе исторических данных"""
    try:
      # Проверяем актуальность кэша
      if (self.correlation_cache['timestamp'] and
          (datetime.datetime.now() - self.correlation_cache['timestamp']).seconds < self.correlation_cache[
            'valid_period']):
        return self.correlation_cache['matrix']

      # Получаем исторические данные для всех символов
      cursor = self.db_manager.conn.cursor()
      correlations = {}

      for sym1 in symbols:
        cursor.execute("""
                SELECT close_price, open_timestamp 
                FROM trades 
                WHERE symbol = ? AND status = 'CLOSED'
                ORDER BY open_timestamp DESC LIMIT 1000
            """, (sym1,))
        data1 = cursor.fetchall()

        if not data1:
          continue

        prices1 = pd.Series([x[0] for x in data1])
        correlations[sym1] = {}

        for sym2 in symbols:
          if sym1 == sym2:
            correlations[sym1][sym2] = 1.0
            continue

          cursor.execute("""
                    SELECT close_price 
                    FROM trades 
                    WHERE symbol = ? AND status = 'CLOSED'
                    ORDER BY open_timestamp DESC LIMIT 1000
                """, (sym2,))
          data2 = cursor.fetchall()

          if not data2:
            continue

          prices2 = pd.Series([x[0] for x in data2])

          # Выравниваем по длине
          min_len = min(len(prices1), len(prices2))
          corr = prices1.iloc[:min_len].corr(prices2.iloc[:min_len])
          correlations[sym1][sym2] = corr if not pd.isna(corr) else 0.0

      # Обновляем кэш
      self.correlation_cache = {
        'timestamp': datetime.datetime.now(),
        'matrix': correlations,
        'valid_period': 3600
      }

      return correlations

    except Exception as e:
      print(f"❌ Ошибка расчета корреляционной матрицы: {e}")
      return {}

  async def get_liquidity_metrics(self, symbol: str, quantity: float) -> Dict[str, float]:
    """Оценивает ликвидность и потенциальное скольжение"""
    try:
      # В реальной реализации здесь будет запрос к API биржи
      order_book = await self._fetch_order_book(symbol)

      if not order_book or 'bids' not in order_book or 'asks' not in order_book:
        return {
          'slippage': 0.0,
          'impact': 0.0,
          'spread': 0.0
        }

      best_bid = order_book['bids'][0][0]
      best_ask = order_book['asks'][0][0]
      spread = best_ask - best_bid

      # Расчет скольжения для заданного объема
      slippage_buy = self._calculate_slippage(order_book['asks'], quantity)
      slippage_sell = self._calculate_slippage(order_book['bids'], quantity, is_bid=True)

      # Расчет рыночного воздействия
      impact = quantity / sum([x[1] for x in order_book['asks'][:5]])

      return {
        'slippage': (slippage_buy + slippage_sell) / 2,
        'impact': impact,
        'spread': spread,
        'best_bid': best_bid,
        'best_ask': best_ask
      }

    except Exception as e:
      print(f"❌ Ошибка оценки ликвидности: {e}")
      return {
        'slippage': 0.0,
        'impact': 0.0,
        'spread': 0.0
      }

  def _calculate_slippage(self, orders: List[Tuple[float, float]], quantity: float, is_bid: bool = False) -> float:
    """Вычисляет ожидаемое скольжение для заданного объема"""
    remaining = quantity
    total_cost = 0.0
    slippage = 0.0

    for price, size in orders:
      if remaining <= 0:
        break

      fill_size = min(remaining, size)
      total_cost += fill_size * price
      remaining -= fill_size

    if quantity > 0:
      avg_price = total_cost / quantity
      if is_bid:
        slippage = (orders[0][0] - avg_price) / orders[0][0]
      else:
        slippage = (avg_price - orders[0][0]) / orders[0][0]

    return slippage

  def _get_active_symbols(self, current_symbol: str) -> List[str]:
    """Интеграция с существующей логикой"""
    cursor = self.db_manager.conn.cursor()
    cursor.execute("""
        SELECT DISTINCT symbol 
        FROM trades 
        WHERE status = 'OPEN' AND symbol != ?
    """, (current_symbol,))
    return [row[0] for row in cursor.fetchall()]


  async def validate_signal(self, signal: TradingSignal, symbol: str, account_balance: float) -> Dict[str, Any]:
    """Расширенная валидация сигнала с учетом корреляции и ликвидности"""
    validation_result = {
        'approved': False,
        'recommended_size': 0.0,
        'risk_score': 0.0,
        'liquidity_metrics': None,
        'correlation_risk': 0.0,
        'warnings': [],
        'reasons': []
    }

    # 1. Проверка уверенности
    if signal.confidence < self.min_confidence_threshold:
        validation_result['warnings'].append(f"Низкая уверенность: {signal.confidence:.2%}")
        validation_result['reasons'].append("Сигнал отклонен из-за низкой уверенности")
        return validation_result

    # 2. Проверка дневного лимита потерь (НОВОЕ)
    daily_loss = await self._check_daily_loss(account_balance)
    if daily_loss['exceeded']:
        validation_result['warnings'].append(f"Превышен дневной лимит потерь: {daily_loss['current']:.2%}/{daily_loss['limit']:.2%}")
        validation_result['reasons'].append("Дневной лимит потерь превышен")
        return validation_result

    # 3. Расчет размера позиции на основе риска
    risk_per_trade = abs(signal.price - signal.stop_loss) / signal.price
    max_loss_per_trade = account_balance * 0.01  # 1% от депозита на сделку

    if risk_per_trade > 0:
      base_size = min(
        max_loss_per_trade / (signal.price * risk_per_trade),
        account_balance * self.max_position_size_percent / signal.price
      )
    else:
      base_size = account_balance * 0.02 / signal.price

    # 4. Оценка ликвидности
    liquidity = await self.get_liquidity_metrics(symbol, base_size)
    validation_result['liquidity_metrics'] = liquidity

    if liquidity['slippage'] > self.slippage_threshold:
      # Автоматическое уменьшение размера позиции
      reduction_factor = min(0.7, self.slippage_threshold / liquidity['slippage'])
      base_size *= reduction_factor
      validation_result['warnings'].append(
        f"Высокое скольжение: {liquidity['slippage']:.2%}. Размер уменьшен в {reduction_factor:.1f} раз"
      )

    # 5. Корреляционный анализ
    active_symbols = self._get_active_symbols(symbol)
    corr_matrix = await self.calculate_correlation_matrix(active_symbols + [symbol])

    if corr_matrix:
      max_corr = max(
        [corr_matrix.get(symbol, {}).get(s, 0) for s in active_symbols],
        default=0
      )
      validation_result['correlation_risk'] = max_corr

      if max_corr > self.correlation_threshold:
        base_size *= 0.6  # Значительное уменьшение при высокой корреляции
        validation_result['warnings'].append(
          f"Высокая корреляция ({max_corr:.2f}) с открытыми позициями"
        )

    # 6. Проверка волатильности
    volatility = await self._check_volatility_risk(symbol, signal.price)
    if volatility > self.volatility_threshold:
      base_size *= 0.5
      validation_result['warnings'].append("Экстремальная волатильность рынка")

    # 7. Финальная проверка минимального размера
    if base_size < account_balance * 0.001:  # Минимум 0.1%
      validation_result['reasons'].append("Размер позиции слишком мал")
      return validation_result

    validation_result.update({
      'approved': True,
      'recommended_size': base_size,
      'risk_score': risk_per_trade,
      'reasons': ["Сигнал одобрен с учетом всех факторов риска"]
    })

    return validation_result

  async def _fetch_order_book(self, symbol: str, depth: int = 25) -> Dict[str, List]:
    """
    Получает стакан ордеров с биржи через connector
    с обработкой ошибок и fallback-логикой

    Args:
        symbol: Торговый символ (например 'BTCUSDT')
        depth: Глубина стакана (по умолчанию 25 уровней)

    Returns:
        Словарь с ключами 'bids' и 'asks', где каждый элемент - список [цена, объем]
        Пример: {'bids': [[50000, 1.5], [49900, 2.3]], 'asks': [[50100, 2.1], [50200, 1.8]]}
    """
    try:
      # Основной запрос через connector
      orderbook = await self.connector.fetch_order_book(symbol, depth)

      # Валидация структуры ответа
      if not isinstance(orderbook, dict) or 'bids' not in orderbook or 'asks' not in orderbook:
        raise ValueError("Некорректная структура стакана от биржи")

      # Нормализация данных
      normalized_bids = []
      for bid in orderbook['bids']:
        if len(bid) >= 2 and isinstance(bid[0], (int, float)) and isinstance(bid[1], (int, float)):
          normalized_bids.append([float(bid[0]), float(bid[1])])

      normalized_asks = []
      for ask in orderbook['asks']:
        if len(ask) >= 2 and isinstance(ask[0], (int, float)) and isinstance(ask[1], (int, float)):
          normalized_asks.append([float(ask[0]), float(ask[1])])

      logger.debug(f"Получен стакан для {symbol}: {len(normalized_bids)} bids, {len(normalized_asks)} asks")
      return {
        'bids': normalized_bids,
        'asks': normalized_asks,
        'timestamp': int(time.time() * 1000),  # Мс timestamp
        'symbol': symbol
      }

    except Exception as e:
      logger.error(f"Ошибка получения стакана для {symbol}: {str(e)}")

      # Fallback: пытаемся получить через CCXT напрямую
      try:
        if hasattr(self.connector, 'exchange'):
          ccxt_orderbook = await self.connector.exchange.fetch_order_book(symbol, limit=depth)
          return {
            'bids': ccxt_orderbook['bids'],
            'asks': ccxt_orderbook['asks'],
            'timestamp': ccxt_orderbook['timestamp'],
            'symbol': symbol
          }
      except Exception as ccxt_e:
        logger.error(f"CCXT fallback также failed для {symbol}: {str(ccxt_e)}")

      # Ultimate fallback - пустой стакан
      return {
        'bids': [],
        'asks': [],
        'timestamp': int(time.time() * 1000),
        'symbol': symbol
      }

  async def _check_daily_loss(self, account_balance: float) -> Dict[str, Any]:
    """Проверяет, превышен ли дневной лимит потерь"""
    cursor = self.db_manager.conn.cursor()
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0)

    cursor.execute("""
        SELECT SUM(profit_loss) 
        FROM trades 
        WHERE status = 'CLOSED' AND open_timestamp >= ? AND profit_loss < 0
    """, (today,))

    total_loss = cursor.fetchone()[0] or 0
    loss_percent = abs(total_loss) / account_balance
    limit_percent = self.max_daily_loss_percent

    return {
      'exceeded': loss_percent >= limit_percent,
      'current': loss_percent,
      'limit': limit_percent,
      'amount': total_loss
    }


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
    """Обновить отображение риск-метрик"""
    try:
      if hasattr(self, 'trade_executor') and self.trade_executor:
        symbols = getattr(self.trade_executor.trading_system, 'active_symbols', []) + [None]
      else:
        symbols = [None]

      self.risk_metrics_table.setRowCount(len(symbols))

      for row, symbol in enumerate(symbols):
        # Получаем метрики
        if hasattr(self, 'trade_executor') and hasattr(self.trade_executor, 'risk_manager'):
          metrics = self.trade_executor.risk_manager.get_risk_metrics(symbol)
        else:
          metrics = None

        # Безопасное заполнение таблицы
        symbol_text = symbol if symbol else "Общие"
        self.risk_metrics_table.setItem(row, 0, QTableWidgetItem(symbol_text))

        if metrics:
          # Безопасное получение атрибутов с значениями по умолчанию
          win_rate = getattr(metrics, 'win_rate', 0.0)
          max_drawdown = getattr(metrics, 'max_drawdown', 0.0)
          profit_factor = getattr(metrics, 'profit_factor', 0.0)
          total_pnl = getattr(metrics, 'total_pnl', 0.0)
          sharpe_ratio = getattr(metrics, 'sharpe_ratio', 0.0)

          self.risk_metrics_table.setItem(row, 1, QTableWidgetItem(f"{win_rate:.1%}"))
          self.risk_metrics_table.setItem(row, 2, QTableWidgetItem(f"{max_drawdown:.2%}"))
          self.risk_metrics_table.setItem(row, 3, QTableWidgetItem(f"{profit_factor:.2f}"))
          self.risk_metrics_table.setItem(row, 4, QTableWidgetItem(f"{total_pnl:.2f}"))
          self.risk_metrics_table.setItem(row, 5, QTableWidgetItem(f"{sharpe_ratio:.2f}"))
        else:
          # Заполняем пустыми значениями
          for col in range(1, 6):
            self.risk_metrics_table.setItem(row, col, QTableWidgetItem("N/A"))

    except Exception as e:
      print(f"Ошибка при обновлении риск-метрик: {e}")


      # """Обновляет риск-метрики в базе данных"""
      # risk_metrics = self.db_manager.get_risk_metrics(symbol, days=30)
      #
      # query = '''
      #           INSERT INTO risk_metrics (
      #               timestamp, symbol, current_drawdown, max_drawdown, win_rate,
      #               profit_factor, sharpe_ratio, daily_pnl
      #           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      #       '''
      #
      # try:
      #   # Рассчитываем дополнительные метрики
      #   max_drawdown = abs(risk_metrics.current_drawdown) * 1.2  # Примерное значение
      #   profit_factor = abs(risk_metrics.avg_profit_loss) / 0.01 if risk_metrics.avg_profit_loss < 0 else 2.0
      #
      #   self.db_manager.conn.execute(query, (
      #     datetime.datetime.now(), symbol, risk_metrics.current_drawdown,
      #     max_drawdown, risk_metrics.win_rate, profit_factor,
      #     risk_metrics.sharpe_ratio, risk_metrics.avg_profit_loss
      #   ))
      #   self.db_manager.conn.commit()
      #
      #   print(f"✅ Риск-метрики обновлены для {symbol or 'всех символов'}")
      # except sqlite3.Error as e:
      #   print(f"❌ Ошибка обновления риск-метрик: {e}")

  # ==============================================================================
  # ИНТЕГРИРОВАННАЯ ТОРГОВАЯ СИСТЕМА
  # ==============================================================================
class SignalProcessor:
    """Отдельный процессор для верификации сигналов"""

    def __init__(self, risk_manager: AdvancedRiskManager):
      self.risk_manager = risk_manager

    async def verify_signal(self, signal: TradingSignal, symbol: str, balance: float) -> Dict[str, Any]:
      return await self.risk_manager.validate_signal(signal, symbol, balance)


class TradeExecutor:
  """Отдельный исполнитель торговых ордеров"""

  def __init__(self, db_manager: AdvancedDatabaseManager):
    self.db_manager = db_manager

  async def execute_order(self, trade_decision: Dict[str, Any]) -> bool:
    # Логика исполнения ордера
    orderbook = await self._fetch_order_book(symbol)

    if not orderbook['bids'] or not orderbook['asks']:
      raise Exception(f"Не удалось получить стакан для {symbol}")
    signal = trade_decision['signal']
    order_id = trade_decision['order_id']
    quantity = trade_decision['recommended_size']

    trade_id = self.db_manager.add_trade_with_signal(signal, order_id, quantity)
    return trade_id is not None


class MLFeedbackLoop:
  """Цикл обратной связи для улучшения ML модели"""

  def __init__(self, db_manager: AdvancedDatabaseManager, ml_strategy: EnsembleMLStrategy):
    self.db_manager = db_manager
    self.ml_strategy = ml_strategy

  async def update_model_with_results(self, symbol: str):
    # Получаем результаты недавних сделок
    cursor = self.db_manager.conn.cursor()
    cursor.execute("""
            SELECT profit_loss, confidence, metadata 
            FROM trades 
            WHERE symbol = ? AND status = 'CLOSED' 
            AND close_timestamp >= datetime('now', '-7 days')
        """, (symbol,))

    results = cursor.fetchall()
    if results:
      # Обновляем веса модели на основе результатов
      await self.ml_strategy.retrain_with_feedback(symbol, results)

class IntegratedTradingSystem:
    """Главная интегрированная торговая система"""

    def __init__(self, db_manager: AdvancedDatabaseManager = None,
                 db_path: str = "advanced_trading.db",
                 connector=None, ml_model=None):
    #def __init__(self, db_path: str = "advanced_trading.db"):
      if db_manager is not None:
        self.db_manager = db_manager
      else:
        # Иначе создаем новый по пути
        self.db_manager = AdvancedDatabaseManager(db_path)

      self.connector = connector
      self.strategy_name = "IntegratedTradingSystem"  # Добавляем имя стратегии

      self.ml_strategy = EnsembleMLStrategy(self.db_manager, ml_model=ml_model)
      self.risk_manager = AdvancedRiskManager(self.db_manager, connector=None)

      self.signal_processor = SignalProcessor(self.risk_manager)
      # self.trade_executor = TradeExecutor(self.db_manager)
      self.ml_feedback = MLFeedbackLoop(self.db_manager, self.ml_strategy)

      self.active_symbols = []
      self.account_balance = 10000.0  # Начальный баланс
      self.running = False

      self.active_strategies = {
            "Ensemble_ML_Strategy": self.ml_strategy
        }

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
      """Обрабатывает рыночные данные через активные стратегии и генерирует торговые решения"""

      if symbol not in self.active_symbols:
        return None

      trade_decisions = []

      # Обрабатываем данные через все активные стратегии
      for strategy_name, strategy in self.active_strategies.items():
        try:
          # Генерируем сигнал через текущую стратегию
          signal = await strategy.generate_signals(symbol, data)

          if not signal:
            trade_decisions.append({'action': 'no_signal', 'symbol': symbol, 'strategy': strategy_name})
            continue

          # Валидируем сигнал через риск-менеджер
          validation = await self.risk_manager.validate_signal(signal, symbol, self.account_balance)

          if not validation['approved']:
            trade_decisions.append({
              'action': 'signal_rejected',
              'symbol': symbol,
              'strategy': strategy_name,
              'signal': signal,
              'validation': validation
            })
            continue

          # Проверяем ликвидность перед исполнением
          if validation['liquidity_metrics']['impact'] > 0.2:
            logger.warning(f"Высокое рыночное воздействие для {symbol}. Используем TWAP стратегию")
            await self.execute_twap_order(signal, validation)
          else:
            await self.execute_market_order(signal, validation)

          # Создаем торговое решение
          trade_decision = {
            'action': 'execute_trade',
            'symbol': symbol,
            'strategy': strategy_name,
            'signal': signal,
            'validation': validation,
            'recommended_size': validation['recommended_size'],
            'risk_score': validation['risk_score'],
            'order_id': f"{symbol}_{strategy_name}_{int(datetime.datetime.now().timestamp())}"
          }

          logger.info(f"🎯 Торговое решение для {symbol} (стратегия {strategy_name}):")
          logger.info(f"   Действие: {signal.signal.value}")
          logger.info(f"   Размер: {validation['recommended_size']:.6f}")
          logger.info(f"   Уверенность: {signal.confidence:.2%}")
          logger.info(f"   Риск-счет: {validation['risk_score']:.2%}")

          trade_decisions.append(trade_decision)

        except Exception as e:
          logger.error(f"❌ Ошибка обработки данных для {symbol} стратегией {strategy_name}: {e}")
          trade_decisions.append({
            'action': 'error',
            'symbol': symbol,
            'strategy': strategy_name,
            'error': str(e)
          })

      # Возвращаем все решения по стратегиям
      return trade_decisions

    def add_strategy(self, strategy: BaseStrategy):
        """Добавляет стратегию в торговую систему"""
        if strategy_name not in self.active_strategies:
          self.active_strategies[strategy_name] = strategy_instance
          logger.info(f"Стратегия '{strategy_name}' добавлена в систему")

    def remove_strategy(self, strategy_name: str):
      """Удаляет стратегию из системы"""
      if strategy_name in self.active_strategies:
        del self.active_strategies[strategy_name]
        logger.info(f"Стратегия '{strategy_name}' удалена из системы")



    async def execute_trade_decision(self, trade_decision: Dict[str, Any]) -> bool:
      """Выполняет торговое решение с учетом стратегии"""

      if trade_decision['action'] != 'execute_trade':
        return False
      strategy_name = trade_decision.get('strategy', 'Unknown')
      signal = trade_decision['signal']
      signal.metadata['strategy'] = strategy_name

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