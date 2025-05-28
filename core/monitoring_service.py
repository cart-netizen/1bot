import asyncio
from typing import List, Set, Callable, Awaitable

from core.data_fetcher import DataFetcher
from logger_setup import get_logger
from config import MONITORING_INTERVAL_SECONDS

logger = get_logger(__name__)


class MonitoringService:
  def __init__(self, data_fetcher: DataFetcher,
               on_list_updated: Callable[[List[str]], Awaitable[None]]):
    self.data_fetcher = data_fetcher
    self.active_symbols_list: Set[str] = set()  # Используем set для быстрого добавления/удаления и уникальности
    self.is_running = False
    self._task: Optional[asyncio.Task] = None
    self.on_list_updated = on_list_updated  # Callback-функция для уведомления об обновлении списка

  async def _fetch_and_filter_symbols(self):
    """Основная логика получения и фильтрации символов."""
    logger.info("Сервис мониторинга: начало обновления списка отслеживаемых символов...")
    try:
      all_usdt_symbols = await self.data_fetcher.get_all_usdt_perpetual_symbols()
      if not all_usdt_symbols:
        logger.warning("Сервис мониторинга: не удалось получить список всех USDT perpetual символов.")
        return

      logger.info(
        f"Сервис мониторинга: получено {len(all_usdt_symbols)} USDT perpetual символов. Фильтрация по объему...")
      high_volume_symbols = await self.data_fetcher.filter_symbols_by_volume(all_usdt_symbols)

      new_symbols_set = set(high_volume_symbols)

      if self.active_symbols_list != new_symbols_set:
        added = new_symbols_set - self.active_symbols_list
        removed = self.active_symbols_list - new_symbols_set

        if added: logger.info(f"Сервис мониторинга: добавлены символы в Список №1: {added}")
        if removed: logger.info(f"Сервис мониторинга: удалены символы из Списка №1: {removed}")

        self.active_symbols_list = new_symbols_set
        logger.info(f"Сервис мониторинга: Список №1 обновлен. Всего символов: {len(self.active_symbols_list)}. "
                    f"Пример: {list(self.active_symbols_list)[:5]}")
        # Уведомляем подписчика об обновлении списка
        if self.on_list_updated:
          await self.on_list_updated(list(self.active_symbols_list))  # Передаем копию в виде списка
      else:
        logger.info("Сервис мониторинга: Список №1 не изменился.")

    except Exception as e:
      logger.error(f"Сервис мониторинга: ошибка в цикле обновления: {e}", exc_info=True)

  async def _run_monitoring_loop(self):
    logger.info("Сервис мониторинга запущен.")
    while self.is_running:
      await self._fetch_and_filter_symbols()
      try:
        await asyncio.sleep(MONITORING_INTERVAL_SECONDS)
      except asyncio.CancelledError:
        logger.info("Сервис мониторинга: цикл обновления остановлен (CancelledError).")
        break
    logger.info("Сервис мониторинга: цикл обновления завершен.")

  async def start(self):
    if not self.is_running:
      self.is_running = True
      # Первый запуск сразу, потом по интервалу
      await self._fetch_and_filter_symbols()
      self._task = asyncio.create_task(self._run_monitoring_loop())
      logger.info("Сервис мониторинга успешно запущен в фоновом режиме.")
    else:
      logger.warning("Сервис мониторинга уже запущен.")

  async def stop(self):
    if self.is_running and self._task:
      self.is_running = False
      self._task.cancel()
      try:
        await self._task
      except asyncio.CancelledError:
        logger.info("Сервис мониторинга: задача успешно отменена.")
      self._task = None
      logger.info("Сервис мониторинга остановлен.")
    else:
      logger.warning("Сервис мониторинга не был запущен или уже остановлен.")

  def get_active_symbols(self) -> List[str]:
    return list(self.active_symbols_list)

# Пример использования:
# async def handle_list_update(updated_list: List[str]):
#    logger.info(f"Callback: Список отслеживаемых символов обновлен, содержит {len(updated_list)} элементов.")
#    # Здесь основное приложение может перенастроить свои стратегии или обработчики данных

# async def main_test_monitoring():
#     from core.bybit_connector import BybitConnector
#     from logger_setup import setup_logging
#     setup_logging("INFO")

#     connector = BybitConnector()
#     await connector.init_session()
#     fetcher = DataFetcher(connector)
#     monitor_service = MonitoringService(fetcher, on_list_updated=handle_list_update)

#     await monitor_service.start()

#     try:
#         # Даем сервису поработать некоторое время (например, чуть больше интервала обновления)
#         await asyncio.sleep(MONITORING_INTERVAL_SECONDS + 10)
#     finally:
#         logger.info("Остановка сервиса мониторинга...")
#         await monitor_service.stop()
#         await connector.close_session()
#         logger.info("Тест сервиса мониторинга завершен.")

# if __name__ == "__main__":
#     asyncio.run(main_test_monitoring())