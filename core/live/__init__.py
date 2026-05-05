"""Live paper-trading runtime for the Value Council.

Mirrors the backtest engine's discipline (PIT data, transaction costs,
strict accounting) but operates on real-time prices and persists
portfolio state across days. Each agent gets a JSON file in
``data/portfolios/<agent>.json`` updated at every market-open run.
"""
