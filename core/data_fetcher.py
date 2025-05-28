import pandas as pd
from typing import List, Dict, Optional, Any
import asyncio

from core.bybit_connector import BybitConnector
from logger_setup import get_logger
from config import MIN_24H_VOLUME_USDT, BYBIT_CATEGORY

logger = get_logger(__name__)


class DataFetcher:
  def __init__(self, connector: BybitConnector):
    self.connector = connector

  async def get_all_usdt_perpetual_symbols(self) -> List[str]:
    """
    Получает список всех торгующихся USDT бессрочных контрактов.
    """
    logger.info(f"Запрос информации обо всех инструментах для категории: {BYBIT_CATEGORY}")
    instruments = await self.connector.get_instruments_info(category=BYBIT_CATEGORY)
    if not instruments:
      logger.warning("Не удалось получить информацию об инструментах.")
      return []

    # Фильтруем по статусу 'Trading' и типу контракта (если есть такая информация)
    # Для USDT-M контрактов обычно quoteCoin будет USDT
    usdt_symbols = [
      inst['symbol'] for inst in instruments
      if inst.get('status') == 'Trading' and inst.get('quoteCoin') == 'USDT'
      # Убедитесь, что фильтр корректен для USDT-M
    ]
    logger.info(f"Найдено {len(usdt_symbols)} торгующихся USDT Perpetual символов.")
    return usdt_symbols

  async def filter_symbols_by_volume(self, symbols: List[str]) -> List[str]:
    """
    Фильтрует символы по суточному объему торгов.
    """
    if not symbols:
      return []

    logger.info(f"Фильтрация {len(symbols)} символов по объему > ${MIN_24H_VOLUME_USDT:,.0f} USDT...")

    # Получаем все тикеры
    tickers_data = await self.connector.fetch_tickers()
    if not tickers_data:
      logger.error("Не удалось получить данные тикеров для фильтрации по объему.")
      return []

    high_volume_symbols = []

    for symbol in symbols:
      # Ищем тикер по полю 'info.symbol', а не по ключу
      ticker = next(
        (t for t in tickers_data.values()
         if t.get('info', {}).get('symbol') == symbol),
        None
      )

      if ticker:
        # Поиск оборота — сначала в info, потом в стандартизированном поле
        volume_24h_usdt = float(ticker.get('info', {}).get('turnover24h', 0)) \
          if 'info' in ticker and isinstance(ticker['info'], dict) \
          else float(ticker.get('quoteVolume', 0))

        if volume_24h_usdt >= MIN_24H_VOLUME_USDT:
          high_volume_symbols.append(symbol)
          logger.debug(f"Символ {symbol} прошел фильтр по объему: ${volume_24h_usdt:,.2f} USDT")
        # else:
        #     logger.debug(f"Символ {symbol} НЕ прошел фильтр по объему: ${volume_24h_usdt:,.2f} USDT")
      else:
        logger.warning(f"Нет данных тикера для символа {symbol} при фильтрации по объему.")

    logger.info(f"Найдено {len(high_volume_symbols)} символов с объемом > ${MIN_24H_VOLUME_USDT:,.0f} USDT.")
    return high_volume_symbols

  async def get_historical_data(self, symbol: str, timeframe: str = '5m', limit: int = 200) -> pd.DataFrame:
    """
    Получает исторические данные (свечи) и возвращает их в виде pandas DataFrame.
    """
    logger.debug(f"Запрос исторических данных для {symbol}, таймфрейм {timeframe}, лимит {limit}")
    # ohlcv_data = await self.connector.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    # Используем прямой вызов kline, т.к. он возвращает более подробные данные для Bybit
    ohlcv_raw = await self.connector.get_kline(symbol=symbol, interval=self.map_timeframe_to_bybit(timeframe),
                                               limit=limit)

    if not ohlcv_raw:
      logger.warning(f"Нет исторических данных для {symbol} ({timeframe}).")
      return pd.DataFrame()

    # Преобразование в DataFrame
    # Формат от Bybit get_kline: список словарей {'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'}
    df = pd.DataFrame(ohlcv_raw)
    if df.empty:
      return df

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df.astype({
      'open': 'float',
      'high': 'float',
      'low': 'float',
      'close': 'float',
      'volume': 'float',
      'turnover': 'float'
    })
    # Сортировка по индексу (времени) на всякий случай, хотя Bybit обычно возвращает в правильном порядке
    df.sort_index(inplace=True)
    logger.info(f"Получены и обработаны исторические данные для {symbol}: {len(df)} строк.")
    return df

  def map_timeframe_to_bybit(self, timeframe_str: str) -> str:
    """
    Конвертирует стандартные обозначения таймфреймов в формат Bybit API.
    Примеры: '1m' -> '1', '1h' -> '60', '1d' -> 'D'
    """
    mapping = {
      '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
      '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
      '1d': 'D', '1w': 'W', '1M': 'M'  # '1M' для месяца
    }
    if timeframe_str in mapping:
      return mapping[timeframe_str]
    logger.warning(f"Неизвестный таймфрейм '{timeframe_str}', используется как есть.")
    return timeframe_str  # Возвращаем как есть, если нет в маппинге


# Пример использования
async def main_test_fetcher():
  from core.bybit_connector import BybitConnector
  from logger_setup import setup_logging
  setup_logging("INFO")

  connector = BybitConnector()
  await connector.init_session()
  fetcher = DataFetcher(connector)

  all_symbols = await fetcher.get_all_usdt_perpetual_symbols()
  if all_symbols:
    logger.info(f"Всего USDT Perpetual символов: {len(all_symbols)}, первые 5: {all_symbols[:5]}")

    # Фильтрация по объему (может быть долгой, если много символов и делается много запросов)
    # Вместо этого, лучше получить все тикеры один раз и фильтровать локально.
    # high_volume_list = await fetcher.filter_symbols_by_volume(all_symbols[:20]) # Тестируем на первых 20
    high_volume_list = await fetcher.filter_symbols_by_volume(all_symbols)
    logger.info(f"Символы с высоким объемом: {high_volume_list}")

    if high_volume_list:
      test_symbol = high_volume_list[0]
      logger.info(f"Получение исторических данных для {test_symbol}...")
      historical_df = await fetcher.get_historical_data(test_symbol, timeframe='5m', limit=10)
      if not historical_df.empty:
        print(f"\nИсторические данные для {test_symbol} (последние {len(historical_df)} свечей):")
        print(historical_df.head())
      else:
        logger.warning(f"Не удалось получить исторические данные для {test_symbol}.")
  else:
    logger.warning("Не удалось получить список символов.")

  await connector.close_session()


if __name__ == "__main__":
  asyncio.run(main_test_fetcher())