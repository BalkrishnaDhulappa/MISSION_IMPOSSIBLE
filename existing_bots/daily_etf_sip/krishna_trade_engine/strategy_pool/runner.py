from strategy_pool.manager import StrategyManager

manager = StrategyManager()

strategy = manager.pick_strategy()

print("Selected Strategy:")
print(strategy)

manager.mark_tested(strategy["name"])
