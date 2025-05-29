import asyncio
import signal
import sys

import pandas as pd
from PyQt6.QtWidgets import QApplication
from typing import List, Dict

from TG_bot.telegram_bot import TelegramBotNotifier
from logger_setup import setup_logging, get_logger
import config  # Загрузка конфигурации
from core.bybit_connector import BybitConnector
from core.data_fetcher import DataFetcher
from core.database_manager_new import DatabaseManager
from core.trade_executor import TradeExecutor
from core.monitoring_service import MonitoringService
from ml_models.lorentzian_classifier import LorentzianClassifier  # и его обучение/загрузка
from strategies.MultiIndicatorStrategy import MultiIndicatorStrategy
from strategies.rsi_ml_strategy import RsiMlStrategy
from strategies.ma_crossover_strategy import MACrossoverStrategy
# from strategies.bollinger_bands_strategy import
from strategies.base_strategy import BaseStrategy
from config import TELEGRAM_TOKEN,TELEGRAM_CHAT_ID
from gui.main_window import MainWindow  # Наше GUI

# Глобальный логгер для main
logger = None

# Список отслеживаемых символов ("Список №1"), обновляемый MonitoringService
# Этот список будет использоваться для запуска/остановки задач по обработке стратегий
tracked_symbols_list: List[str] = []
strategy_tasks: Dict[str, asyncio.Task] = {}  # Задачи для каждой стратегии/символа

# --- Инициализация компонентов ---
# Эти объекты будут создаваться в main_async
bybit_connector:[BybitConnector] = None
data_fetcher:[DataFetcher] = None
db_manager:[DatabaseManager] = None
trade_executor:[TradeExecutor] = None
ml_model:[LorentzianClassifier] = None
# Словарь стратегий {name: instance}
strategy_instances: Dict[str, BaseStrategy] = {}
monitoring_service:[MonitoringService] = None
main_window:[MainWindow] = None  # Для GUI
app:[QApplication] = None  # Для GUI


async def process_symbol_strategy(symbol: str, strategy: BaseStrategy, fetcher: DataFetcher, executor: TradeExecutor):
  """
  Асинхронная задача для обработки одной стратегии для одного символа.
  Эта функция будет запускаться для каждого символа из "Списка №1".
  """
  logger.info(f"Запуск обработки стратегии '{strategy.strategy_name}' для символа {symbol}...")
  try:
    while True:
      # 1. Получить свежие данные для символа
      # Таймфрейм и лимит должны быть параметрами стратегии или глобальными
      # Для примера, 5-минутные свечи, последние 200 штук для индикаторов
      # ВАЖНО: Убедитесь, что get_historical_data не создает слишком большую нагрузку на API,
      # если вызывается очень часто для многих символов. Возможно, стоит использовать WebSocket для получения klines.
      market_data_df = await fetcher.get_historical_data(symbol, timeframe='5m', limit=200)

      if market_data_df.empty:
        logger.warning(f"[{strategy.strategy_name}/{symbol}] Нет данных для обработки.")
        await asyncio.sleep(60)  # Подождать перед следующей попыткой
        continue

      # Дополняем DataFrame нужными колонками, если их нет (для pandas-ta)
      if 'high' not in market_data_df.columns: market_data_df['high'] = market_data_df['close']
      if 'low' not in market_data_df.columns: market_data_df['low'] = market_data_df['close']

      # 2. Сгенерировать сигнал
      signal_info = await strategy.generate_signals(symbol, market_data_df)

      if signal_info:
        logger.info(f"[{strategy.strategy_name}/{symbol}] Получен сигнал: {signal_info}")
        # 3. Исполнить сделку (если сигнал BUY или SELL)
        # Здесь нужна логика определения размера позиции (position sizing)
        # Для примера, фиксированное количество или % от баланса
        # ВАЖНО: Управление риском и размером позиции - критическая часть!
        quantity = 0.001  # Пример для BTCUSDT (нужно получать из minOrderQty для символа)

        # Получение информации об инструменте для мин. кол-ва
        # instruments_info = await fetcher.connector.get_instruments_info(symbol=symbol)
        # if instruments_info and instruments_info[0].get('lotSizeFilter'):
        #     min_qty = float(instruments_info[0]['lotSizeFilter'].get('minOrderQty', "0.001"))
        #     quantity = max(quantity, min_qty) # Убедимся, что не меньше минимального

        # Проверка, есть ли уже открытая позиция по этой стратегии/символу, чтобы избежать дублирования
        # (требует более сложного управления состоянием)

        if signal_info['signal'] == "BUY" or signal_info['signal'] == "SELL":
          await executor.execute_trade(
            symbol=symbol,
            side=signal_info['signal'].lower(),
            quantity=quantity,  # Заменить на рассчитанное количество
            strategy_name=strategy.strategy_name,
            order_type="Market",  # Или 'Limit' с ценой из signal_info
            price=signal_info.get('price'),  # Для лимитных
            leverage=config.LEVERAGE,
            stop_loss=signal_info.get('stop_loss'),
            take_profit=signal_info.get('take_profit')
          )
        # Если сигнал 'CLOSE', то нужно вызвать executor.close_position()
        # Это требует, чтобы стратегия могла генерировать и такие сигналы,
        # и чтобы executor.close_position() мог найти нужную позицию для закрытия.

      # Пауза перед следующей проверкой (например, каждую минуту или по закрытию свечи)
      # Если таймфрейм 5m, то проверять можно раз в 1-5 минут.
      # Идеально - синхронизироваться с закрытием свечей.
      await asyncio.sleep(60)  # Проверять каждую минуту (для примера)

  except asyncio.CancelledError:
    logger.info(f"Обработка стратегии '{strategy.strategy_name}' для {symbol} остановлена.")
  except Exception as e:
    logger.error(f"Ошибка в обработке стратегии '{strategy.strategy_name}' для {symbol}: {e}", exc_info=True)
    # Здесь можно добавить логику перезапуска задачи или уведомления


async def on_monitored_list_updated(updated_symbols: List[str]):
  """
  Callback, который вызывается, когда MonitoringService обновляет "Список №1".
  Запускает/останавливает задачи обработки стратегий для символов.
  """
  global tracked_symbols_list, strategy_tasks, strategy_instances, data_fetcher, trade_executor
  logger.info(f"MAIN: Список отслеживаемых символов обновлен: {len(updated_symbols)} символов.")

  current_symbols_set = set(tracked_symbols_list)
  new_symbols_set = set(updated_symbols)

  symbols_to_add = new_symbols_set - current_symbols_set
  symbols_to_remove = current_symbols_set - new_symbols_set

  # Останавливаем задачи для удаленных символов
  for symbol in symbols_to_remove:
    if symbol in strategy_tasks:
      logger.info(f"MAIN: Остановка задачи для символа {symbol}.")
      strategy_tasks[symbol].cancel()
      try:
        await strategy_tasks[symbol]  # Ждем завершения отмены
      except asyncio.CancelledError:
        pass  # Ожидаемо
      del strategy_tasks[symbol]

  # Запускаем задачи для новых символов
  # Предположим, мы используем одну и ту же основную стратегию для всех символов из списка №1
  # В будущем можно будет назначать разные стратегии разным символам.
  main_strategy_name = "RSI_ML_Strategy"  # или другая выбранная стратегия

  if main_strategy_name not in strategy_instances:
    logger.error(f"MAIN: Основная стратегия '{main_strategy_name}' не найдена в экземплярах стратегий!")
    return

  active_strategy = strategy_instances[main_strategy_name]

  for symbol in symbols_to_add:
    if symbol not in strategy_tasks:  # Если уже есть (маловероятно), не перезапускаем
      logger.info(f"MAIN: Запуск задачи для символа {symbol} со стратегией {active_strategy.strategy_name}.")
      task = asyncio.create_task(process_symbol_strategy(symbol, active_strategy, data_fetcher, trade_executor))
      strategy_tasks[symbol] = task

  tracked_symbols_list = updated_symbols
  if main_window:  # Если GUI запущен, уведомляем его
    main_window.worker.active_symbols_updated.emit(tracked_symbols_list)


def get_current_monitored_symbols() -> List[str]:
  """Функция-геттер для GUI, чтобы получить текущий Список №1."""
  return tracked_symbols_list


async def main_async():
  """Основная асинхронная функция для запуска логики бота."""
  global logger, bybit_connector, data_fetcher, db_manager, trade_executor, ml_model
  global strategy_instances, monitoring_service, main_window, app


  # 0. Настройка логирования (вынесена наверх, чтобы логгер был доступен сразу)
  logger = get_logger("MainApp")  # Логгер для этого файла

  logger.info("Запуск торгового бота Bybit...")

  # 1. Инициализация компонентов
  # API ключи должны быть в config.py (из .env)
  if not config.API_KEY or not config.API_SECRET:
    logger.critical("API_KEY или API_SECRET не установлены! Бот не сможет выполнять большинство операций.")
    # Можно либо выйти, либо продолжить в режиме "только просмотр", если это предусмотрено.
    # Для примера, продолжим, но многие вещи не будут работать.

  bybit_connector = BybitConnector(api_key=config.API_KEY, api_secret=config.API_SECRET)
  await bybit_connector.init_session()  # Инициализация HTTP сессии

  data_fetcher = DataFetcher(bybit_connector)
  db_manager = DatabaseManager(db_path=config.DATABASE_PATH)
  trade_executor = TradeExecutor(bybit_connector, db_manager)
  # telegram_bot = TelegramBotNotifier(token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID)
  # trade_executor = TradeExecutor(connector=bybit_connector, db_manager=db_manager, telegram_bot=telegram_bot)
  # await telegram_bot.start_polling()


  # 2. Инициализация и обучение/загрузка ML модели
  # ВАЖНО: Этот этап требует реальных данных и процесса обучения.
  # Сейчас это заглушка.
  ml_model = LorentzianClassifier(n_neighbors=8)  # Параметры из конфига или по умолчанию
  try:
    # Попытка загрузить предобученную модель
    # ml_model.load_model("path/to/your/trained_lorentzian_model.pkl")
    logger.info("ML модель (LorentzianClassifier-заглушка) инициализирована. Пропустите обучение для примера.")
    # Если нет обученной, то нужно обучить (требует данных)
    # X_train, y_train = await data_fetcher.get_training_data_for_ml() # Гипотетический метод
    # if X_train is not None and y_train is not None:
    #    ml_model.fit(X_train, y_train)
    #    logger.info("ML модель обучена.")
    # else:
    #    logger.warning("Не удалось получить данные для обучения ML модели.")
    # Для примера, "обучим" заглушку, если она не была "обучена" при инициализации
    if not ml_model.is_fitted:
      # В LorentzianClassifier есть example_train_and_predict_lorentzian,
      # но он использует случайные данные. Для реального бота это не подходит.
      # Пока что считаем, что модель либо загружена, либо будет использоваться как есть (если может без fit).
      # В RsiMlStrategy мы проверяем ml_model.is_fitted.
      logger.warning("ML модель не была обучена или загружена. RSI_ML_Strategy может работать некорректно.")
      # Можно сделать фиктивное обучение для теста
      dummy_X = pd.DataFrame({'rsi': [50, 30, 70], 'some_other_feature': [1, 2, 3]})
      dummy_y = pd.Series([0, 1, 2])
      ml_model.fit(dummy_X, dummy_y)  # "Обучаем" на минимальных данных
      if ml_model.is_fitted:
        logger.info("ML модель (заглушка) фиктивно 'обучена'.")


  except Exception as e:
    logger.error(f"Ошибка при инициализации/обучении ML модели: {e}", exc_info=True)
    # Решить, может ли бот работать без ML модели или с базовой логикой

  # 3. Инициализация экземпляров стратегий
  strategy_instances["RSI_ML_Strategy"] = RsiMlStrategy(ml_model=ml_model)
  strategy_instances["MA_Crossover"] = MACrossoverStrategy(params={"short_ma_period": 10, "long_ma_period": 30})
  strategy_instances["MultiIndicatorStrategy"] = MultiIndicatorStrategy(params={})
  # ... инициализация других стратегий ...
  logger.info(f"Загружено {len(strategy_instances)} стратегий: {list(strategy_instances.keys())}")

  # 4. Запуск сервиса мониторинга (Список №1)
  monitoring_service = MonitoringService(data_fetcher, on_list_updated=on_monitored_list_updated)
  await monitoring_service.start()

  # 5. Запуск GUI (если требуется)
  # GUI запускается в основном потоке, asyncio event loop будет работать в фоновом потоке,
  # созданном для worker'а GUI или в этом же потоке, если GUI интегрирован с asyncio (например, через qasync).
  # Для классического PyQt, asyncio обычно работает в отдельном потоке.

  # Передаем async_loop в MainWindow, чтобы оно могло ставить задачи
  # main_window.async_loop = asyncio.get_event_loop() # Это будет event loop текущего (main_async) потока

  # Запуск приложения PyQt должен быть последним блокирующим вызовом в основном потоке.
  # Все асинхронные задачи (мониторинг, стратегии) должны быть запущены до этого.
  # Асинхронный код будет выполняться в event loop'е.

  # Основной цикл приложения (если нет GUI, или GUI не блокирующий)
  # Этот цикл нужен, чтобы бот продолжал работать и обрабатывать сигналы завершения
  stop_event = asyncio.Event()  # Событие для ожидания сигнала завершения

  # Настройка обработчиков сигналов для корректного завершения
  loop = asyncio.get_event_loop()
  for sig_name in ('SIGINT', 'SIGTERM'):
    if sys.platform == 'win32' and sig_name == 'SIGTERM':  # SIGTERM не всегда работает на Windows
      continue
    try:
      loop.add_signal_handler(getattr(signal, sig_name),
                              lambda s=sig_name: asyncio.create_task(shutdown(s, loop, stop_event)))
    except NotImplementedError:  # Например, на Windows для SIGINT в не-консольных приложениях
      logger.warning(f"Не удалось установить обработчик для сигнала {sig_name}.")



  logger.info("Торговый бот запущен. Нажмите Ctrl+C для выхода.")

  # Если GUI используется, он должен быть запущен здесь, и он будет блокировать.
  # Если нет GUI, или GUI запускается неблокирующе, то ждем события остановки.
  if app and main_window:  # Если GUI был инициализирован
    logger.info("GUI запущен. Асинхронные задачи работают в фоне.")
    # Event loop Qt будет управлять основным потоком.
    # Event loop asyncio (для бота) должен работать в другом потоке или интегрирован.
    # В нашем случае, BackendWorker для GUI работает в своем QThread с asyncio loop.
    # А основной asyncio loop (этот) продолжает работать для monitoring_service и стратегий.

  await stop_event.wait()  # Ожидаем сигнала на завершение

  logger.info("Получен сигнал на завершение. Финальные операции...")
  # Здесь можно добавить ожидание завершения всех активных задач, если это необходимо.


async def shutdown(sig: str, loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event):
  """Чистит и останавливает все компоненты."""
  logger.warning(f"Получен сигнал {sig}. Начинаю процедуру остановки...")

  # 1. Останавливаем задачи стратегий
  logger.info("Остановка задач стратегий...")
  for symbol, task in list(strategy_tasks.items()):  # list() для копии, т.к. словарь может меняться
    if not task.done():
      task.cancel()
      try:
        await task
      except asyncio.CancelledError:
        logger.info(f"Задача для {symbol} отменена.")
      except Exception as e:
        logger.error(f"Ошибка при отмене задачи для {symbol}: {e}", exc_info=True)
    if symbol in strategy_tasks:  # Проверка на случай, если задача удалилась сама
      del strategy_tasks[symbol]

  # 2. Останавливаем сервис мониторинга
  if monitoring_service:
    logger.info("Остановка сервиса мониторинга...")
    await monitoring_service.stop()

  # 3. Закрываем соединение с Bybit (важно для корректного освобождения ресурсов)
  if bybit_connector:
    logger.info("Закрытие соединения с Bybit API...")
    await bybit_connector.close_session()

  # 4. Закрываем соединение с БД
  if db_manager:
    logger.info("Закрытие соединения с БД...")
    db_manager.close_connection()  # Это синхронный метод

  # 5. Остановка GUI (если он есть и управляется отсюда)
  if app and main_window:
    logger.info("Попытка закрытия GUI...")
    # main_window.close() # Это вызовет closeEvent в MainWindow
    # Закрытие GUI должно происходить в его основном потоке.
    # QApplication.quit()
    pass  # GUI сам закроется, если был запущен из основного потока

  logger.info("Все компоненты остановлены. Завершение работы.")
  stop_event.set()  # Устанавливаем событие, чтобы главный цикл завершился

  # Остановка event loop (если это не делается автоматически после stop_event.set())
  # tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
  # for task in tasks:
  #    task.cancel()
  # await asyncio.gather(*tasks, return_exceptions=True)
  # loop.stop()


if __name__ == "__main__":
  setup_logging(config.LOG_LEVEL)  # Настраиваем логирование глобально

  # Инициализация QApplication ДО создания MainWindow и ДО запуска asyncio loop, если GUI в основном потоке
  app = QApplication(sys.argv)

  # Создаем компоненты, которые нужны GUI сразу (но не запускаем их async части)
  # Эти экземпляры будут переданы в MainWindow
  temp_connector = BybitConnector(api_key=config.API_KEY, api_secret=config.API_SECRET)
  # await temp_connector.init_session() # НЕЛЬЗЯ await здесь, т.к. это синхронная часть __main__
  # Инициализацию сессии нужно будет сделать в async части или при запуске worker'а GUI

  temp_db_manager = DatabaseManager(db_path=config.DATABASE_PATH)  # DB manager синхронный
  # temp_data_fetcher создастся внутри main_async или будет передан из него

  # --- Запуск PyQt GUI ---
  # MainWindow будет инициализирован с необходимыми компонентами.
  # Его BackendWorker будет запускать асинхронные задачи в своем потоке.
  # Создадим временные заглушки, т.к. полная инициализация в main_async
  # В реальности, main_window должен создаваться ПОСЛЕ инициализации основных компонентов в main_async
  # или получать их через callback/сигналы.

  # Это сложный момент: как правильно передать полностью инициализированные async компоненты в GUI,
  # который запускается синхронно.
  # Вариант 1: GUI инициализируется с "пустыми" компонентами, а потом они "оживают".
  # Вариант 2: Запустить asyncio loop, инициализировать все, потом передать в GUI и запустить GUI.
  #            Это требует интеграции Qt event loop с asyncio event loop (qasync).
  # Вариант 3 (принятый здесь): MainWindow создает своего BackendWorker, который уже работает с asyncio.
  #            Основные компоненты (connector, fetcher) создаются в main_async и передаются в worker.

  # Для передачи компонентов в GUI, создадим их здесь, а init_session и прочее будет в main_async
  # и потом в worker'е.
  # Это значит, что BybitConnector, DataFetcher, TradeExecutor должны быть созданы
  # ДО запуска main_async или переданы в него и затем в GUI.
  # Либо, GUI получает "фабрики" для их создания внутри своего BackendWorker.

  # Проще всего, если main_async запускается, инициализирует все,
  # а потом запускает GUI, передав ему готовые компоненты.
  # Но QApplication.exec() блокирующий.

  # Используем такой подход:
  # 1. Создаем QApplication.
  # 2. Запускаем основной asyncio loop в отдельном потоке.
  # 3. В этом asyncio loop инициализируем все компоненты.
  # 4. Создаем MainWindow и передаем ему компоненты (или ссылки на них).
  # 5. Запускаем QApplication.exec() в основном потоке.
  # 6. При закрытии GUI/сигнале SIGINT, останавливаем asyncio loop.

  # Для упрощения, предположим, что main_async инициализирует глобальные переменные,
  # а GUI их использует. Это не самый чистый способ, но для примера.
  # Более чисто - main_async создает и передает экземпляры.

  # --- Обновленный подход для __main__ ---
  event_loop = None
  bot_thread = None

  try:
    # Создаем и запускаем основной asyncio event loop в отдельном потоке
    def run_async_bot():
      global event_loop
      event_loop = asyncio.new_event_loop()
      asyncio.set_event_loop(event_loop)
      try:
        event_loop.run_until_complete(main_async())
      except KeyboardInterrupt:  # Хотя у нас есть обработчики SIGINT
        logger.info("Asyncio loop прерван (KeyboardInterrupt).")
      finally:
        logger.info("Asyncio loop завершен.")
        # event_loop.close() # Закрытие loop может вызвать проблемы, если есть незавершенные задачи


    import threading

    bot_thread = threading.Thread(target=run_async_bot, name="BotAsyncThread")
    bot_thread.daemon = True  # Поток завершится, если основной поток завершится
    bot_thread.start()

    # Даем время на инициализацию компонентов в потоке бота
    # Это плохая практика, лучше использовать события или condition variables для синхронизации.
    # Для примера, просто небольшая пауза.
    import time

    time.sleep(5)  # Ждем, пока bybit_connector и др. инициализируются в main_async

    # Теперь компоненты должны быть доступны через глобальные переменные (не лучший паттерн)
    # или через функции-геттеры, если бы мы их реализовали.
    # Для GUI важно, чтобы bybit_connector, db_manager, data_fetcher, trade_executor были доступны.
    # В main_async мы их присваиваем глобальным переменным.

    if bybit_connector and db_manager and data_fetcher and trade_executor and strategy_instances:
      main_window = MainWindow(
        connector = bybit_connector,
        db_manager = db_manager,
        data_fetcher = data_fetcher,
        trade_executor = trade_executor,
        monitoring_service_getter=get_current_monitored_symbols,
        strategy_instances=strategy_instances
      )
      # Передаем event_loop из потока бота в GUI, чтобы он мог ставить туда задачи
      main_window.async_loop = event_loop
      main_window.show()
      logger.info("GUI MainWindow запущен.")
      sys.exit(app.exec())  # Запуск главного цикла Qt
    else:
      logger.error("Не удалось инициализировать основные компоненты для GUI. Завершение.")
      # Попытка остановить поток бота, если он еще работает
      if event_loop and event_loop.is_running():
        # Отправляем сигнал на завершение в event_loop
        stop_event_placeholder = asyncio.Event()  # Нужен доступ к реальному stop_event
        # event_loop.call_soon_threadsafe(stop_event_placeholder.set) # Это не сработает так просто
        # Лучше, если shutdown можно вызвать извне.
        # На данном этапе, если компоненты не создались, просто выходим.
        # Корректная остановка потока сложна без прямой связи.
        pass


  except KeyboardInterrupt:
    logger.info("Приложение прервано пользователем (Ctrl+C в основном потоке).")
  except Exception as e_main:
    logger.critical(f"Критическая ошибка в __main__: {e_main}", exc_info=True)
  finally:
    logger.info("Завершение работы основного потока приложения.")
    if event_loop and event_loop.is_running():
      logger.info("Попытка остановить asyncio event loop...")
      # Это должно быть сделано через механизм shutdown в самом event loop
      # Например, event_loop.call_soon_threadsafe(lambda: asyncio.create_task(shutdown(...)))
      # Но shutdown уже должен быть вызван сигналами SIGINT/SIGTERM внутри loop.
      # Если мы вышли по другой причине, loop может еще работать.
      # event_loop.call_soon_threadsafe(event_loop.stop) # Один из способов
    if bot_thread and bot_thread.is_alive():
      logger.info("Ожидание завершения потока бота...")
      bot_thread.join(timeout=10)
      if bot_thread.is_alive():
        logger.warning("Поток бота не завершился в течение 10 секунд.")

    logger.info("Программа завершена.")

