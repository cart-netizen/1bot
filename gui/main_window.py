import asyncio
import sys
import ccxt.async_support as ccxt

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel,
                             QTextEdit, QLineEdit, QComboBox, QSplitter, QTabWidget, QMenuBar, QCheckBox)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction

import pyqtgraph as pg  # Для графиков
import pandas as pd
from datetime import datetime

from logger_setup import get_logger
# Импорты других ваших модулей
from core.bybit_connector import BybitConnector
from core.database_manager_new import AdvancedDatabaseManager
# from core.database_manager import DatabaseManager
from core.trade_executor import TradeExecutor
from core.data_fetcher import DataFetcher  # Для получения данных для графиков
from config import LEVERAGE, API_KEY, API_SECRET
from core.strategy_manager import StrategyManager
logger = get_logger(__name__)


# Для взаимодействия GUI с асинхронным бэкендом
class BackendWorker(QObject):
  balance_updated = pyqtSignal(dict)
  positions_updated = pyqtSignal(list)
  db_trades_updated = pyqtSignal(list)
  log_message = pyqtSignal(str)
  active_symbols_updated = pyqtSignal(list)
  chart_data_updated = pyqtSignal(str, pd.DataFrame)
  account_balance_history_updated = pyqtSignal(pd.DataFrame)

  def __init__(self, db_manager: AdvancedDatabaseManager):
    super().__init__()
    self.db_manager = None
    self.is_running = True
    self.loop = None

    self.connector = None
    self.exchange = None
    self.data_fetcher = None
    self.trade_executor = None
    self.current_symbol_for_chart = None

  def start_event_loop(self):
    self.db_manager = AdvancedDatabaseManager()
    self.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self.loop)

    # Создаём CCXT внутри нужного event loop
    self.exchange = ccxt.bybit({
      'apiKey': API_KEY,
      'secret': API_SECRET,
      'enableRateLimit': True,
      'timeout': 30000,
      'options': {
        'defaultType': 'linear',
        'adjustForTimeDifference': True,
        'recvWindow': 20000
      }
    })

    # Передаём exchange в коннектор и другие зависимости
    self.connector = BybitConnector(api_key=API_KEY, api_secret=API_SECRET)
    self.connector.exchange = self.exchange
    self.data_fetcher = DataFetcher(self.connector)
    self.trade_executor = TradeExecutor(self.connector, self.db_manager)
    #---------------------------------------------------------------------------------------------
    try:
      # Попробуем установить более короткий таймаут для одного вызова
      self.loop.run_until_complete(
        asyncio.wait_for(self.exchange.load_markets(), timeout=10)
      )
      logger.info("CCXT markets loaded successfully.")
    except asyncio.TimeoutError:
      logger.warning("Timeout при загрузке markets CCXT. Работаем без полной загрузки рынков.")
    except Exception as e:
      logger.error(f"Ошибка при загрузке markets CCXT: {e}", exc_info=True)
    #--------------------------------------------------------------------------------------было self.loop.run_until_complete(self.exchange.
    # load_markets())

    #self.loop.run_until_complete(self.exchange.load_markets())
    self.loop.create_task(self.run_tasks())

    try:
      self.loop.run_forever()
    finally:
      self.loop.run_until_complete(self.exchange.close())
      self.loop.close()

  async def run_tasks(self):
    logger.info("BackendWorker: Запуск асинхронных задач.")
    count = 0
    while self.is_running:
      try:
        if self.connector.api_key and self.connector.api_secret:
          balance_data = await self.connector.get_account_balance()
          if balance_data:
            self.balance_updated.emit(balance_data)

          positions_data = await self.connector.fetch_positions()
          if positions_data:
            self.positions_updated.emit(positions_data)

          if count % 5 == 0:
            db_trades = self.db_manager.get_all_trades(limit=50)
            self.db_trades_updated.emit(db_trades)
            self.update_balance_chart_data()

        if self.current_symbol_for_chart and count % 2 == 0:
          pass  # можно запросить график тут, если надо

        await asyncio.sleep(10)
        count += 1
      except Exception as e:
        self.log_message.emit(f"Ошибка в BackendWorker: {e}")
        logger.error(f"Ошибка в BackendWorker: {e}", exc_info=True)
        await asyncio.sleep(30)

  def update_balance_chart_data(self):
    all_trades = self.db_manager.get_all_trades(limit=1000)
    if not all_trades:
      return

    for trade in all_trades:
      for field in ['close_timestamp', 'open_timestamp']:
        if isinstance(trade.get(field), str):
          try:
            trade[field] = datetime.fromisoformat(trade[field])
          except Exception:
            continue

    all_trades.sort(key=lambda x: x.get('close_timestamp') or x.get('open_timestamp') or datetime.min)

    balance_history = []
    balance = 10000
    for trade in all_trades:
      timestamp = trade.get('close_timestamp') or trade.get('open_timestamp')
      pnl = trade.get('profit_loss', 0.0) or 0.0
      if trade.get('status') == 'CLOSED' and trade.get('close_timestamp'):
        balance += pnl
        balance_history.append({'timestamp': timestamp, 'balance': balance})
      elif not balance_history:
        balance_history.append({'timestamp': timestamp, 'balance': balance})

    if balance_history:
      df = pd.DataFrame(balance_history)
      df['timestamp'] = pd.to_datetime(df['timestamp'])
      df.set_index('timestamp', inplace=True)
      self.account_balance_history_updated.emit(df)

  def stop_event_loop(self):
    self.is_running = False
    if self.loop and self.loop.is_running():
      self.loop.call_soon_threadsafe(self.loop.stop)

  def manual_close_position(self, symbol: str):
    """Синхронный метод, вызываемый из GUI, который внутри ставит асинхронную задачу в loop."""
    if not self.loop or not self.loop.is_running():
      logger.error("Event loop не активен, невозможно закрыть позицию вручную.")
      return

    async def _close():
      try:
        await self.trade_executor.close_position(symbol)
        self.log_message.emit(f"🔒 Позиция по {symbol} успешно закрыта.")
      except Exception as e:
        self.log_message.emit(f"❌ Ошибка при закрытии позиции {symbol}: {e}")
        logger.error(f"Ошибка при ручном закрытии {symbol}: {e}", exc_info=True)

    asyncio.run_coroutine_threadsafe(_close(), self.loop)


class MainWindow(QMainWindow):
  def __init__(self, connector: BybitConnector, db_manager: AdvancedDatabaseManager, data_fetcher: DataFetcher,
               trade_executor: TradeExecutor,
               monitoring_service_getter: object,  #: Callable[[], List[str]],
               strategy_instances: object) -> object:   #: Dict[str, BaseStrategy]):  # Добавили стратегии
    super().__init__()
    self.connector = connector
    self.db_manager = db_manager
    self.data_fetcher = data_fetcher
    self.trade_executor = trade_executor
    self.get_monitored_symbols = monitoring_service_getter  # Функция для получения Списка №1
    self.strategy_instances = strategy_instances  # Словарь доступных стратегий

    self.setWindowTitle("Торговый Бот Bybit v0.1 (Serious Edition)")
    self.setGeometry(100, 100, 1600, 900)

    # --- Backend Worker в отдельном потоке ---
    self.backend_thread = QThread()
    self.worker = BackendWorker(db_manager=self.db_manager)
    # self.worker = BackendWorker(db_manager=self.db_manager)
    self.worker.moveToThread(self.backend_thread)

    self.backend_thread.started.connect(self.worker.start_event_loop)
    self.backend_thread.start()

    # Подключение сигналов от воркера к слотам GUI
    self.worker.balance_updated.connect(self.update_balance_display)
    #self.worker.open_trades_updated.connect(
      #self.update_open_trades_table)  # Не используется, заменили на positions_updated
    self.worker.positions_updated.connect(self.update_positions_table)
    self.worker.db_trades_updated.connect(self.update_db_trades_table)
    self.worker.log_message.connect(self.add_log_message)
    self.worker.active_symbols_updated.connect(self.update_active_symbols_display)
    self.worker.chart_data_updated.connect(self.update_chart_display)
    self.worker.account_balance_history_updated.connect(self.update_account_balance_chart)

    # # Запуск event loop в потоке воркера
    # self.backend_thread.started.connect(self.worker.start_event_loop)
    # self.backend_thread.start()

    self.init_ui()
    self.load_initial_data()  # Первоначальная загрузка данных

    # Таймер для периодического обновления списка отслеживаемых символов из MonitoringService
    self.monitor_update_timer = QTimer(self)
    self.monitor_update_timer.timeout.connect(self.refresh_monitored_symbols_list)
    self.monitor_update_timer.start(15000)  # Каждые 15 секунд (Список №1 обновляется реже)

    self.strategy_instances = strategy_instances
    # Initialize StrategyManager


    self.strategy_manager = StrategyManager(trade_executor)
    # optional: signal callback to log
    self.strategy_manager.set_signal_callback(
    lambda symbol, sig: self.add_log_message(f"Signal {sig['signal']} from {sig['strategy_name']} on {symbol}"))


  def init_ui(self):
    # --- Меню ---
    menubar = self.menuBar()
    file_menu = menubar.addMenu('&Файл')
    exit_action = QAction('&Выход', self)
    exit_action.triggered.connect(self.close)
    file_menu.addAction(exit_action)
    # ... другие меню (Настройки, Помощь)

    # --- Основной виджет и layout ---
    main_widget = QWidget()
    self.setCentralWidget(main_widget)
    # Используем QSplitter для изменения размеров панелей
    main_splitter = QSplitter(Qt.Orientation.Horizontal, self)

    # --- Левая панель (управление, информация) ---
    left_panel = QWidget()
    left_layout = QVBoxLayout(left_panel)

    # --- Стратегии ---
    left_layout.addWidget(QLabel("Стратегии для включения:"))
    self.strategy_checkboxes = {}
    # dynamically get available strategies

    for name in self.strategy_instances.keys():
      cb = QCheckBox(name)
      cb.stateChanged.connect(self.on_strategy_toggled)
      left_layout.addWidget(cb)
      self.strategy_checkboxes[name] = cb



    # Баланс
    self.balance_label = QLabel("Баланс USDT: Загрузка...")
    left_layout.addWidget(self.balance_label)

    # Список отслеживаемых символов (Список №1)
    left_layout.addWidget(QLabel("Список отслеживаемых символов (Список №1):"))
    self.active_symbols_table = QTableWidget()
    self.active_symbols_table.setColumnCount(1)
    self.active_symbols_table.setHorizontalHeaderLabels(["Символ"])
    self.active_symbols_table.itemClicked.connect(self.on_symbol_selected_for_chart)
    left_layout.addWidget(self.active_symbols_table)

    # Ручное управление сделками
    manual_trade_group = QWidget()
    manual_trade_layout = QVBoxLayout(manual_trade_group)
    manual_trade_layout.addWidget(QLabel("Ручное управление:"))

    self.symbol_input = QLineEdit()
    self.symbol_input.setPlaceholderText("Символ (напр. BTCUSDT)")
    self.quantity_input = QLineEdit()
    self.quantity_input.setPlaceholderText("Количество")
    self.price_input = QLineEdit()
    self.price_input.setPlaceholderText("Цена (для Limit)")

    self.side_combo = QComboBox()
    self.side_combo.addItems(["BUY", "SELL"])
    self.order_type_combo = QComboBox()
    self.order_type_combo.addItems(["Market", "Limit"])

    open_button = QPushButton("Открыть позицию")
    open_button.clicked.connect(self.handle_manual_open)
    self.close_pos_symbol_input = QLineEdit()  # Для ввода символа закрываемой позиции
    self.close_pos_symbol_input.setPlaceholderText("Символ для закрытия")
    close_button = QPushButton("Закрыть позицию (Рыночный)")
    close_button.clicked.connect(self.handle_manual_close)

    form_layout = QHBoxLayout()
    form_layout.addWidget(self.symbol_input)
    form_layout.addWidget(self.quantity_input)
    form_layout.addWidget(self.price_input)
    manual_trade_layout.addLayout(form_layout)

    controls_layout = QHBoxLayout()
    controls_layout.addWidget(self.side_combo)
    controls_layout.addWidget(self.order_type_combo)
    controls_layout.addWidget(open_button)
    manual_trade_layout.addLayout(controls_layout)

    close_controls_layout = QHBoxLayout()
    close_controls_layout.addWidget(QLabel("Закрыть:"))
    close_controls_layout.addWidget(self.close_pos_symbol_input)
    close_controls_layout.addWidget(close_button)
    manual_trade_layout.addLayout(close_controls_layout)

    left_layout.addWidget(manual_trade_group)

    # Логи
    left_layout.addWidget(QLabel("Логи:"))
    self.log_output = QTextEdit()
    self.log_output.setReadOnly(True)
    left_layout.addWidget(self.log_output)

    main_splitter.addWidget(left_panel)

    # --- Правая панель (графики, таблицы сделок) ---
    right_panel = QWidget()
    right_layout = QVBoxLayout(right_panel)

    # Вкладки для графиков и таблиц
    self.tabs = QTabWidget()

    # Вкладка 1: График цены и индикаторов
    self.chart_tab = QWidget()
    chart_tab_layout = QVBoxLayout(self.chart_tab)

    self.chart_widget_pg = pg.PlotWidget()  # pyqtgraph PlotWidget
    # Настройка внешнего вида графика (TradingView-like - это сложно, делаем базово)
    self.chart_widget_pg.setBackground('w')  # Белый фон
    self.chart_widget_pg.showGrid(x=True, y=True, alpha=0.3)
    self.price_plot_item = self.chart_widget_pg.plot([], [], pen=pg.mkPen('b', width=2), name="Цена")  # Синий для цены
    # Сюда можно добавлять другие индикаторы (RSI, MA) на этот же график или на subplots
    # self.rsi_plot_item = self.chart_widget_pg.plot([], [], pen=pg.mkPen('g', width=1), name="RSI")

    # Для свечного графика нужен CandlestickItem (более сложная настройка)
    # self.candle_item = pg.CandlestickItem([], []) # Данные: (time, open, high, low, close)
    # self.chart_widget_pg.addItem(self.candle_item)

    chart_tab_layout.addWidget(self.chart_widget_pg)
    self.tabs.addTab(self.chart_tab, "График Цены")

    # Вкладка 2: Открытые позиции (с биржи)
    self.positions_tab = QWidget()
    positions_layout = QVBoxLayout(self.positions_tab)
    self.positions_table = QTableWidget()
    self.positions_table.setColumnCount(
      8)  # Symbol, Side, Size, Entry Price, Mark Price, Liq Price, P&L (unrealized), Leverage
    self.positions_table.setHorizontalHeaderLabels(
      ["Символ", "Сторона", "Размер", "Цена Входа", "Марк. Цена", "Цена Ликв.", "P/L (Нереал.)", "Плечо"])
    positions_layout.addWidget(self.positions_table)
    self.tabs.addTab(self.positions_tab, "Открытые Позиции (Биржа)")

    # Вкладка 3: История сделок (из БД)
    self.db_trades_tab = QWidget()
    db_trades_layout = QVBoxLayout(self.db_trades_tab)
    self.db_trades_table = QTableWidget()
    # ID, Symbol, OrderID, Strategy, Side, OpenTime, CloseTime, OpenPrice, ClosePrice, Qty, Leverage, P/L, Commission, Status
    self.db_trades_table.setColumnCount(14)
    self.db_trades_table.setHorizontalHeaderLabels([
      "ID", "Символ", "OrderID", "Стратегия", "Сторона", "Время Откр.", "Время Закр.",
      "Цена Откр.", "Цена Закр.", "Кол-во", "Плечо", "P/L", "Комиссия", "Статус"
    ])
    db_trades_layout.addWidget(self.db_trades_table)
    self.tabs.addTab(self.db_trades_tab, "История Сделок (БД)")

    # Вкладка 4: График динамики баланса
    self.balance_chart_tab = QWidget()
    balance_chart_layout = QVBoxLayout(self.balance_chart_tab)
    self.balance_chart_widget_pg = pg.PlotWidget()
    self.balance_chart_widget_pg.setBackground('w')
    self.balance_chart_widget_pg.showGrid(x=True, y=True, alpha=0.3)
    self.balance_plot_item = self.balance_chart_widget_pg.plot([], [], pen=pg.mkPen('g', width=2),
                                                               name="Динамика Баланса")
    # Настройка оси времени для графика баланса
    axis = pg.DateAxisItem(orientation='bottom')
    self.balance_chart_widget_pg.setAxisItems({'bottom': axis})
    balance_chart_layout.addWidget(self.balance_chart_widget_pg)
    self.tabs.addTab(self.balance_chart_tab, "Динамика Баланса")

    right_layout.addWidget(self.tabs)
    main_splitter.addWidget(right_panel)
    main_splitter.setSizes([400, 1200])  # Начальные размеры панелей

    main_layout_overall = QHBoxLayout(main_widget)  # Используем QHBoxLayout для QSplitter
    main_layout_overall.addWidget(main_splitter)

    self.add_log_message("GUI инициализирован.")

  def on_strategy_toggled(self, state):
    # Called when user toggles strategy enable/disable

    sender = self.sender()
    strat_name = sender.text()
    enabled = sender.isChecked()
    # Update StrategyManager
    symbols = self.get_monitored_symbols()

    for symbol in symbols:

      if enabled:
        strat = self.strategy_instances[strat_name]
      self.strategy_manager.register_strategy(symbol, strat)
    else:
      self.strategy_manager.unregister_strategy(symbol, strat_name)



  def load_initial_data(self):
    """Загружает начальные данные при запуске GUI."""
    self.add_log_message("Загрузка начальных данных...")
    self.refresh_monitored_symbols_list()  # Обновить список №1
    # Запрос начальных данных через worker (он уже должен быть запущен)
    # Worker сам периодически обновляет, но можно инициировать первый запрос явно, если нужно

  def refresh_monitored_symbols_list(self):
    """Обновляет отображение списка отслеживаемых символов."""
    symbols = self.get_monitored_symbols()  # Получаем из MonitoringService
    self.update_active_symbols_display(symbols)

  # --- Слоты для обновления GUI ---
  def update_balance_display(self, balance_data: dict):
    # { 'free': ..., 'used': ..., 'total': ... } - ожидаемый формат от CCXT для USDT
    total_balance = balance_data.get('total', 'N/A')
    free_balance = balance_data.get('free', 'N/A')
    self.balance_label.setText(f"Баланс USDT: Всего={total_balance} | Доступно={free_balance}")

  def update_positions_table(self, positions: list):
    self.positions_table.setRowCount(0)  # Очистить таблицу
    if not positions:
      return
    self.positions_table.setRowCount(len(positions))
    for row, pos in enumerate(positions):
      # Данные из CCXT fetch_positions
      self.positions_table.setItem(row, 0, QTableWidgetItem(str(pos.get('symbol'))))
      side = pos.get('side', 'N/A')  # 'long' or 'short' or None
      contracts = float(pos.get('contracts', 0))
      display_side = "BUY" if side == 'long' else ("SELL" if side == 'short' else "N/A")
      if side is None and contracts != 0:  # Пытаемся определить по размеру, если 'side' нет
        display_side = "BUY" if contracts > 0 else "SELL"

      self.positions_table.setItem(row, 1, QTableWidgetItem(display_side))
      self.positions_table.setItem(row, 2, QTableWidgetItem(f"{contracts:.4f}"))  # Размер
      self.positions_table.setItem(row, 3, QTableWidgetItem(str(pos.get('entryPrice', 'N/A'))))
      self.positions_table.setItem(row, 4, QTableWidgetItem(str(pos.get('markPrice', 'N/A'))))
      self.positions_table.setItem(row, 5, QTableWidgetItem(str(pos.get('liquidationPrice', 'N/A'))))
      self.positions_table.setItem(row, 6, QTableWidgetItem(f"{float(pos.get('unrealisedPnl', 0)):.2f}"))
      self.positions_table.setItem(row, 7, QTableWidgetItem(str(pos.get('leverage', 'N/A'))))
    self.positions_table.resizeColumnsToContents()

  def update_db_trades_table(self, trades: list):
    self.db_trades_table.setRowCount(0)
    if not trades:
      return
    self.db_trades_table.setRowCount(len(trades))
    for row, trade in enumerate(trades):
      self.db_trades_table.setItem(row, 0, QTableWidgetItem(str(trade.get('id'))))
      self.db_trades_table.setItem(row, 1, QTableWidgetItem(str(trade.get('symbol'))))
      self.db_trades_table.setItem(row, 2, QTableWidgetItem(str(trade.get('order_id'))))
      self.db_trades_table.setItem(row, 3, QTableWidgetItem(str(trade.get('strategy'))))
      self.db_trades_table.setItem(row, 4, QTableWidgetItem(str(trade.get('side'))))
      self.db_trades_table.setItem(row, 5, QTableWidgetItem(str(trade.get('open_timestamp'))))
      self.db_trades_table.setItem(row, 6, QTableWidgetItem(str(trade.get('close_timestamp', 'N/A'))))
      self.db_trades_table.setItem(row, 7, QTableWidgetItem(str(trade.get('open_price'))))
      self.db_trades_table.setItem(row, 8, QTableWidgetItem(str(trade.get('close_price', 'N/A'))))
      self.db_trades_table.setItem(row, 9, QTableWidgetItem(str(trade.get('quantity'))))
      self.db_trades_table.setItem(row, 10, QTableWidgetItem(str(trade.get('leverage'))))
      self.db_trades_table.setItem(row, 11, QTableWidgetItem(
        f"{trade.get('profit_loss', 0.0):.2f}" if trade.get('profit_loss') is not None else "N/A"))
      self.db_trades_table.setItem(row, 12, QTableWidgetItem(
        f"{trade.get('commission', 0.0):.4f}" if trade.get('commission') is not None else "N/A"))
      self.db_trades_table.setItem(row, 13, QTableWidgetItem(str(trade.get('status'))))
    self.db_trades_table.resizeColumnsToContents()

  def add_log_message(self, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    self.log_output.append(f"[{timestamp}] {message}")
    self.log_output.ensureCursorVisible()  # Автопрокрутка

  def update_active_symbols_display(self, symbols: list):
    self.active_symbols_table.setRowCount(0)
    if not symbols: return
    self.active_symbols_table.setRowCount(len(symbols))
    for row, symbol_name in enumerate(sorted(symbols)):  # Сортируем для единообразия
      self.active_symbols_table.setItem(row, 0, QTableWidgetItem(symbol_name))
    self.active_symbols_table.resizeColumnsToContents()
    for strat_name, cb in self.strategy_checkboxes.items():
      if cb.isChecked():
        strat = self.strategy_instances[strat_name]
        for sym in symbols:
          self.strategy_manager.register_strategy(sym, strat)

  def on_symbol_selected_for_chart(self, item: QTableWidgetItem):
    symbol = item.text()
    self.add_log_message(f"Выбран символ для графика: {symbol}")
    self.worker.current_symbol_for_chart = symbol  # Сообщаем воркеру, какой символ обновлять

    # Немедленный запрос данных для выбранного символа (опционально, т.к. воркер и так обновит)
    # asyncio.create_task(self.request_chart_data_for_symbol(symbol))
    # Пока просто ждем, когда воркер обновит. Или можно сделать более явный запрос.
    # Для более быстрого отклика, можно было бы сделать прямой вызов в data_fetcher здесь,
    # но это нарушило бы поток GUI -> Worker.
    # Лучше, если Worker сам следит за current_symbol_for_chart.
    # Чтобы график обновился быстрее при выборе, можно:
    async def temp_fetch():
      klines_df = await self.data_fetcher.get_historical_data(symbol, timeframe='5m', limit=100)
      if not klines_df.empty:
        self.update_chart_display(symbol, klines_df)

    if hasattr(asyncio, 'get_running_loop'):  # Проверяем, запущен ли event loop
      try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
          asyncio.create_task(temp_fetch())
        else:  # Если loop не запущен (маловероятно в потоке воркера, но возможно из основного)
          self.add_log_message("Event loop не запущен для обновления графика.")
      except RuntimeError:  # Если нет текущего event loop
        self.add_log_message("Нет текущего event loop для обновления графика.")
    else:  # Для старых версий Python, где нет get_running_loop
      asyncio.ensure_future(temp_fetch())

  def update_chart_display(self, symbol: str, df: pd.DataFrame):
    if symbol != self.worker.current_symbol_for_chart:  # Если данные пришли для неактуального символа
      return

    self.add_log_message(f"Обновление графика для {symbol} ({len(df)} точек)")
    if 'close' not in df.columns:
      self.add_log_message(f"Ошибка: нет колонки 'close' в данных для графика {symbol}")
      return

    # Преобразуем индекс datetime в числовые значения для pyqtgraph
    # timestamps = df.index.astype(np.int64) // 10**9 # в секунды
    timestamps = [dt.timestamp() for dt in df.index]

    self.price_plot_item.setData(x=timestamps, y=df['close'].values)
    self.chart_widget_pg.setTitle(f"График цены: {symbol}")
    # self.chart_widget_pg.autoRange() # Автомасштабирование

    # Если есть RSI, можно его тоже отобразить (возможно, на отдельном subplot)
    # if 'rsi' in df.columns:
    #     self.rsi_plot_item.setData(x=timestamps, y=df['rsi'].values)

  def update_account_balance_chart(self, df_balance: pd.DataFrame):
    if df_balance.empty:
      self.add_log_message("Нет данных для графика динамики баланса.")
      return

    self.add_log_message(f"Обновление графика динамики баланса ({len(df_balance)} точек)")
    timestamps = [dt.timestamp() for dt in df_balance.index]
    balances = df_balance['balance'].values

    self.balance_plot_item.setData(x=timestamps, y=balances)
    self.balance_chart_widget_pg.setTitle("Динамика Баланса Счета (Симуляция по Закрытым Сделкам)")
    # self.balance_chart_widget_pg.autoRange()

  # --- Обработчики ручного управления ---
  def handle_manual_open(self):
    symbol = self.symbol_input.text().upper().strip()
    quantity_str = self.quantity_input.text().strip()
    price_str = self.price_input.text().strip()
    side = self.side_combo.currentText().lower()
    order_type = self.order_type_combo.currentText()

    if not symbol or not quantity_str:
      self.add_log_message("Ошибка: Символ и количество должны быть указаны для ручного ордера.")
      return
    try:
      quantity = float(quantity_str)
      price = float(price_str) if price_str and order_type.lower() == 'limit' else None
      if quantity <= 0:
        self.add_log_message("Ошибка: Количество должно быть положительным.")
        return
      if order_type.lower() == 'limit' and (price is None or price <= 0):
        self.add_log_message("Ошибка: Цена для лимитного ордера должна быть указана и быть положительной.")
        return

      # Запускаем асинхронную операцию в потоке воркера
      asyncio.run_coroutine_threadsafe(
        self.worker.manual_open_position(symbol, side, quantity, order_type, price),
        asyncio.get_event_loop()
        # Получаем event loop потока, в котором работает воркер (нужно убедиться, что он доступен)
        # Это может быть сложно, если основной поток GUI не имеет своего event loop.
        # Лучше, если бы worker имел метод, который можно вызвать синхронно,
        # а он уже внутри себя ставит задачу в свой asyncio loop.

        # Более простой, но менее изящный способ - прямой вызов, если worker в том же потоке:
        # asyncio.create_task(self.worker.manual_open_position(...))
        # Но worker в другом потоке, поэтому нужен механизм межпоточного вызова async метода.

        # Исправление: Т.к. self.backend_thread.started.connect запускает asyncio.create_task(self.worker.run_tasks()),
        # то у нас есть event loop в self.backend_thread.
        # Можно передать корутину в этот loop.
      ).result(timeout=10)  # Добавим таймаут, чтобы GUI не зависал, если что-то пойдет не так с запуском
      # .result() сделает вызов блокирующим, что не очень хорошо для GUI.
      # Лучше:
      # future = asyncio.run_coroutine_threadsafe(...)
      # future.add_done_callback(lambda f: self.log_message.emit(f"Manual open task status: {f.exception()}"))
      # Но для простоты пока оставим так, или просто не будем ждать result.
      # Главное - задача должна быть поставлена в очередь event loop'а воркера.

      # Наиболее правильный подход: QObject.invokemethod или сигнал-слот для вызова метода воркера,
      # который уже внутри себя (в своем потоке) вызовет async-метод.
      # Для простоты, мы можем положиться на то, что воркер постоянно опрашивает и может принять команду.
      # Либо, если MainApp (см. main.py) имеет доступ к event loop, через него.
      # Проще всего: сделать методы в Worker НЕ async, а внутри них использовать asyncio.create_task.
      # Это избежит сложностей с run_coroutine_threadsafe.
      # Однако, т.к. trade_executor методы async, то и вызывающие их должны быть async.
      # Переделаем BackendWorker так, чтобы его публичные методы для команд были обычными,
      # а внутри они уже вызывали asyncio.create_task(self._async_actual_method())

      # Пока что, для концепта, будем считать, что вызов сработает:
      # (Этот блок нужно будет пересмотреть для корректной работы с потоками и asyncio)
      # Проще всего, если в MainWindow мы создаем задачу для event_loop, который управляет ботом
      if hasattr(self, 'async_loop') and self.async_loop.is_running():
        asyncio.run_coroutine_threadsafe(
          self.worker.manual_open_position(symbol, side, quantity, order_type, price),
          self.async_loop
        )
      else:  # Если главный цикл не доступен так просто
        self.add_log_message("Не удалось поставить задачу на открытие ордера (event loop).")


    except ValueError:
      self.add_log_message("Ошибка: Количество и цена должны быть числами.")
    except Exception as e:
      self.add_log_message(f"Ошибка при попытке ручного открытия: {e}")
      logger.error(f"GUI handle_manual_open error: {e}", exc_info=True)

  def handle_manual_close(self):
    symbol = self.close_pos_symbol_input.text().upper().strip()
    if not symbol:
      self.add_log_message("Ошибка: Символ должен быть указан для закрытия позиции.")
      return

    # Ищем открытую позицию по этому символу в таблице positions_table (данные с биржи)
    # или в db_trades_table (наши записи).
    # Для простоты, предполагаем, что trade_executor.close_position сам разберется,
    # если ему передать только символ.
    # open_order_id можно попытаться найти, если мы его где-то храним для активных позиций в GUI.

    # Ставим задачу в event loop воркера
    if hasattr(self, 'async_loop') and self.async_loop.is_running():
      asyncio.run_coroutine_threadsafe(
        self.worker.manual_close_position(symbol=symbol),  # open_order_id=None
        self.async_loop
      )
    else:
      self.add_log_message("Не удалось поставить задачу на закрытие ордера (event loop).")


  def closeEvent(self, event):
    """Обработка закрытия окна."""
    self.add_log_message("Завершение работы GUI...")
    self.worker.is_running = False  # Сигнал воркеру на остановку
    self.backend_thread.quit()  # Завершаем поток Qt
    self.backend_thread.wait(5000)  # Ждем завершения потока (с таймаутом)

    # Здесь также должна быть логика для корректной остановки всего бота (MonitoringService, стратегий и т.д.)
    # Это должно координироваться из main.py

    super().closeEvent(event)