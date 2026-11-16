from enum import StrEnum


class RiskPreference(StrEnum):
    CAPITAL_PRESERVATION = "capital_preservation"
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"


class IncomeStability(StrEnum):
    STABLE = "stable"
    VARIABLE = "variable"


class RiskCategory(StrEnum):
    CONSERVATIVE = "Conservative"
    MODERATE = "Moderate"
    AGGRESSIVE = "Aggressive"


class Bucket(StrEnum):
    EQUITY = "equity"
    DEBT = "debt"
    GOLD = "gold"
    ALTERNATIVES = "alternatives"


class Action(StrEnum):
    BUY = "BUY"
    INCREASE = "INCREASE"
    HOLD = "HOLD"
    DECREASE = "DECREASE"
    EXIT = "EXIT"


class DailyOutcome(StrEnum):
    HOLD = "HOLD"
    REBALANCE = "REBALANCE"
    ESCALATE = "ESCALATE"

