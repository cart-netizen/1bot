import pandas as pd

import config
from core.database_manager_new import IntegratedTradingSystem
from strategies.MultiIndicatorStrategy import MultiIndicatorStrategy

from typing import Callable, Dict, List
from core.trade_executor import TradeExecutor
from strategies.base_strategy import BaseStrategy

class StrategyManager:
    def __init__(self, trade_executor: TradeExecutor):
        # strategies per symbol: { symbol: { strategy_name: strategy_instance } }
        self.strategies: Dict[str, Dict[str, BaseStrategy]] = {}
        self.trade_executor = trade_executor
        # global signal handler callback: symbol, signal_info
        # self.signal_callback: Callable[[str, Dict], None] = None
        self.trading_system = IntegratedTradingSystem(
            db_manager=trade_executor.db_manager,  # Используем существующий db_manager
            connector=trade_executor.connector
        )
        #__________________________________
        #     db_path=config.DATABASE_PATH,
        #     connector=trade_executor.connector
        # )
        #________________________________________
    def set_signal_callback(self, callback: Callable[[str, Dict], None]):
        self.signal_callback = callback

    def register_strategy(self, symbol: str, strategy: BaseStrategy):
        if symbol not in self.strategies:
            self.strategies[symbol] = {}
        self.strategies[symbol][strategy.strategy_name] = strategy
        # subscribe strategy to call back on signal
        async def _on_signal(s, info):
            if self.signal_callback:
                self.signal_callback(s, info)
        # assume each strategy will call back using this hook
        strategy.signal_callback = _on_signal

    def unregister_strategy(self, symbol: str, strategy_name: str):
        if symbol in self.strategies and strategy_name in self.strategies[symbol]:
            del self.strategies[symbol][strategy_name]

    def get_active_symbols(self) -> List[str]:
        return [sym for sym, st in self.strategies.items() if st]
#---------------------------прошлая реализация async def process_market_data-
    # async def process_market_data(self, symbol: str, data):
    #     # For each registered strategy on this symbol
    #     if symbol in self.strategies:
    #         for strat in self.strategies[symbol].values():
    #             signal = await strat.generate_signals(symbol, data)
    #             if signal:
    #                 # pass to executor
    #                 self.trade_executor.execute_trade(
    #                     symbol=symbol,
    #                     side=signal['signal'],
    #                     quantity=signal.get('quantity', 0.001),
    #                     strategy_name=strat.strategy_name,
    #                     order_type=signal.get('order_type', 'Market'),
    #                     price=signal.get('price'),
    #                     stop_loss=signal.get('stop_loss'),
    #                     take_profit=signal.get('take_profit')
    #                 )
    #                 # notify any listeners
    #                 if self.signal_callback:
    #                     self.signal_callback(symbol, signal)
#-----------------------------------------------------------------------------
    async def process_market_data(self, symbol: str, data: pd.DataFrame):
        """Обработка данных через интегрированную систему"""
        if symbol not in self.trading_system.active_symbols:
            return

        decision = await self.trading_system.process_market_data(symbol, data)

        if decision and decision['action'] == 'execute_trade':
            await self.trade_executor.execute_trade_decision(decision)