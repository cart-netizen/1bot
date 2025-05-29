import sqlite3
import datetime
from typing import Optional, List, Tuple, Any

from logger_setup import get_logger
from config import DATABASE_PATH

logger = get_logger(__name__)


def adapt_datetime(val: datetime.datetime) -> str:
  return val.strftime("%Y-%m-%d %H:%M:%S")


def convert_datetime(val: bytes) -> datetime.datetime:
  return datetime.datetime.strptime(val.decode("utf-8"), "%Y-%m-%d %H:%M:%S")


sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("TIMESTAMP", convert_datetime)

class DatabaseManager:
  def __init__(self, db_path: str = DATABASE_PATH):
    self.db_path = db_path
    self.conn: Optional[sqlite3.Connection] = None
    self.cursor: Optional[sqlite3.Cursor] = None
    self._connect()
    self._create_trades_table()


  def _connect(self):
    """Устанавливает соединение с базой данных SQLite."""
    try:
      self.conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
      self.cursor = self.conn.cursor()
      logger.info(f"Успешное подключение к базе данных: {self.db_path}")
    except sqlite3.Error as e:
      logger.error(f"Ошибка подключения к SQLite: {e}", exc_info=True)
      # В реальном приложении можно предпринять попытки переподключения или выйти





  def _create_trades_table(self):
    """Создает таблицу для хранения информации о сделках, если она еще не существует."""
    if not self.cursor:
      logger.error("Нет курсора БД для создания таблицы.")
      return
    try:
      self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    order_id TEXT UNIQUE,                   -- ID ордера на бирже (может быть полезно)
                    strategy TEXT,                          -- Название стратегии
                    side TEXT,                              -- 'buy' или 'sell'
                    open_timestamp TIMESTAMP,
                    close_timestamp TIMESTAMP,
                    open_price REAL NOT NULL,
                    close_price REAL,
                    quantity REAL NOT NULL,
                    leverage INTEGER,
                    profit_loss REAL,
                    commission REAL,
                    status TEXT DEFAULT 'OPEN'              -- 'OPEN', 'CLOSED', 'CANCELLED'
                )
            """)
      self.conn.commit()
      logger.info("Таблица 'trades' проверена/создана.")
    except sqlite3.Error as e:
      logger.error(f"Ошибка создания таблицы 'trades': {e}", exc_info=True)

  def add_open_trade(self, symbol: str, order_id: str, strategy: str, side: str,
                     open_timestamp: datetime, open_price: float, quantity: float, leverage: int) -> Optional[int]:
    """Добавляет информацию об открытой сделке в БД. Возвращает ID записи."""
    if not self.conn or not self.cursor:
      logger.error("Нет соединения с БД для добавления сделки.")
      return None
    query = """
             INSERT INTO trades (symbol, order_id, strategy, side, open_timestamp, open_price, quantity, leverage, status)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
         """
    try:
      #open_timestamp_str = open_timestamp.isoformat()
      open_timestamp_str = open_timestamp.strftime('%Y-%m-%d %H:%M:%S')
      self.cursor.execute(query, (symbol, order_id, strategy, side, open_timestamp_str, open_price, quantity, leverage))
      self.conn.commit()
      trade_db_id = self.cursor.lastrowid
      logger.info(
        f"Открытая сделка добавлена в БД (ID: {trade_db_id}): {symbol} {side} {quantity} @ {open_price}, OrderID: {order_id}")
      return trade_db_id
    except sqlite3.IntegrityError as e:  # Если order_id уже существует
      logger.error(f"Ошибка добавления открытой сделки (возможно, дубликат order_id {order_id}): {e}", exc_info=True)
    except sqlite3.Error as e:
      logger.error(f"Ошибка SQLite при добавлении открытой сделки: {e}", exc_info=True)
    return None

  def update_close_trade(self, order_id: str, close_timestamp: datetime,
                         close_price: float, profit_loss: float, commission: float) -> bool:
    """Обновляет информацию о закрытой сделке в БД."""
    if not self.conn or not self.cursor:
      logger.error("Нет соединения с БД для обновления сделки.")
      return False
    query = """
            UPDATE trades
            SET close_timestamp = ?, close_price = ?, profit_loss = ?, commission = ?, status = 'CLOSED'
            WHERE order_id = ? AND status = 'OPEN'
        """
    try:
      #close_timestamp_str = close_timestamp.isoformat()
      close_timestamp_str = close_timestamp.strftime('%Y-%m-%d %H:%M:%S')
      self.cursor.execute(query, (close_timestamp_str, close_price, profit_loss, commission, order_id))
      self.conn.commit()
      if self.cursor.rowcount > 0:
        logger.info(
          f"Сделка (OrderID: {order_id}) обновлена как закрытая. P/L: {profit_loss:.2f}, Комиссия: {commission:.4f}")
        return True
      else:
        logger.warning(f"Не найдена открытая сделка с OrderID: {order_id} для закрытия, или она уже закрыта.")
        return False
    except sqlite3.Error as e:
      logger.error(f"Ошибка SQLite при обновлении закрытой сделки (OrderID: {order_id}): {e}", exc_info=True)
      return False

  def get_trade_by_order_id(self, order_id: str) -> Optional[dict[str, Any]]:
    """Получает сделку из БД по ID ордера на бирже."""
    if not self.cursor:
      logger.error("Нет курсора БД для получения сделки.")
      return None
    query = "SELECT * FROM trades WHERE order_id = ?"
    try:
      self.cursor.execute(query, (order_id,))
      row = self.cursor.fetchone()
      if row:
        # Преобразование кортежа в словарь
        columns = [desc[0] for desc in self.cursor.description]
        return dict(zip(columns, row))
      return None
    except sqlite3.Error as e:
      logger.error(f"Ошибка SQLite при получении сделки по order_id {order_id}: {e}", exc_info=True)
      return None

  def get_all_trades(self, limit: int = 100, offset: int = 0) -> List[dict[str, Any]]:
    """Получает все сделки из БД с пагинацией."""
    if not self.cursor: return []
    query = "SELECT * FROM trades ORDER BY open_timestamp DESC LIMIT ? OFFSET ?"
    try:
      self.cursor.execute(query, (limit, offset))
      rows = self.cursor.fetchall()
      columns = [desc[0] for desc in self.cursor.description]
      return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
      logger.error(f"Ошибка SQLite при получении всех сделок: {e}", exc_info=True)
      return []

  def get_open_positions_from_db(self) -> List[dict[str, Any]]:
    """Получает все активные (статус 'OPEN') позиции из БД."""
    if not self.cursor: return []
    query = "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY open_timestamp ASC"
    try:
      self.cursor.execute(query)
      rows = self.cursor.fetchall()
      columns = [desc[0] for desc in self.cursor.description]
      return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
      logger.error(f"Ошибка SQLite при получении открытых позиций из БД: {e}", exc_info=True)
      return []

  def close_connection(self):
    """Закрывает соединение с базой данных."""
    if self.conn:
      self.conn.close()
      logger.info("Соединение с базой данных закрыто.")


# Пример использования
def main_test_db():
  from logger_setup import setup_logging
  setup_logging("DEBUG")

  # Удаляем тестовую БД перед запуском, чтобы начать с чистого листа (только для теста!)
  import os
  if os.path.exists("test_trades.db"):
    os.remove("test_trades.db")

  db_manager = DatabaseManager(db_path="test_trades.db")

  # Добавление открытой сделки
  trade1_id = db_manager.add_open_trade(
    symbol="BTCUSDT",
    order_id="order_btc_123",
    strategy="RSI_ML",
    side="buy",
    open_timestamp=datetime.datetime.now(),
    open_price=50000.0,
    quantity=0.001,
    leverage=10
  )
  logger.info(f"ID добавленной сделки 1: {trade1_id}")

  trade2_id = db_manager.add_open_trade(
    symbol="ETHUSDT",
    order_id="order_eth_456",
    strategy="MA_Crossover",
    side="sell",
    open_timestamp=datetime.datetime.now(),
    open_price=3000.0,
    quantity=0.05,
    leverage=10
  )
  logger.info(f"ID добавленной сделки 2: {trade2_id}")

  # Попытка добавить дубликат
  db_manager.add_open_trade(
    symbol="BTCUSDT",
    order_id="order_btc_123",  # Такой же order_id
    strategy="RSI_ML",
    side="buy",
    open_timestamp=datetime.datetime.now(),
    open_price=50100.0,
    quantity=0.001,
    leverage=10
  )

  # Получение сделки по order_id
  retrieved_trade = db_manager.get_trade_by_order_id("order_btc_123")
  if retrieved_trade:
    logger.info(f"Получена сделка по OrderID order_btc_123: {retrieved_trade}")

  # Обновление (закрытие) сделки
  closed_successfully = db_manager.update_close_trade(
    order_id="order_btc_123",
    close_timestamp=datetime.datetime.now(),
    close_price=51000.0,
    profit_loss=(51000.0 - 50000.0) * 0.001,  # Упрощенно, без учета плеча и комиссий
    commission=0.50
  )
  logger.info(f"Сделка order_btc_123 закрыта успешно: {closed_successfully}")

  retrieved_trade_after_close = db_manager.get_trade_by_order_id("order_btc_123")
  if retrieved_trade_after_close:
    logger.info(f"Сделка order_btc_123 после закрытия: {retrieved_trade_after_close}")

  # Получение открытых позиций
  open_positions = db_manager.get_open_positions_from_db()
  logger.info(f"Открытые позиции в БД ({len(open_positions)}):")
  for pos in open_positions:
    logger.info(f"  {pos}")

  # Получение всех сделок
  all_trades = db_manager.get_all_trades()
  logger.info(f"Все сделки в БД ({len(all_trades)}):")
  for trade_info in all_trades:
    logger.info(f"  {trade_info}")

  db_manager.close_connection()

  # Удаляем тестовую БД после теста
  if os.path.exists("test_trades.db"):
    os.remove("test_trades.db")


if __name__ == "__main__":
  main_test_db()