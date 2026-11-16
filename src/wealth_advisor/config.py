"""Single source of truth for thresholds. Starting assumptions, not advice."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskWeights(BaseModel):
    horizon: float = 0.30
    preference: float = 0.25
    savings: float = 0.20
    diversification: float = 0.15
    liquidity: float = 0.10


class AnalystWeights(BaseModel):
    fundamental: float = 0.35
    fit: float = 0.25
    news: float = 0.20
    sentiment: float = 0.10
    liquidity_cost: float = 0.10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WEALTH_", env_file=".env", extra="ignore", env_nested_delimiter="__"
    )

    # Fund-selection rule set: "set1" (baseline default), "set2" (Magic of MF), or "combined" (merge engine)
    fund_rule_set: Literal["set1", "set2", "combined"] = "set2"

    # Stock Fundamental Analysis Rule Set selector:
    # - "set1": existing equity heuristics (P/E vs sector P/E, earnings growth YoY, D/E <= 2, pledge <= 25%)
    # - "set2": FA Course Notes (10-pointer / 6-pointer bank screen, ratios, valuation, bank metrics) (default)
    stock_rule_set: Literal["set1", "set2"] = "set2"
    stock_sleeve_pct_of_equity: dict[str, float] = {
        "Conservative": 0.0,
        "Moderate": 20.0,
        "Aggressive": 30.0,
    }
    max_weight_per_stock: float = 10.0
    # Trend-based stock allocation (momentum blend, sizing tilt, breadth regime shift of the stock sleeve)
    trend_momentum_weight: float = 0.15
    trend_tilt_min: float = 0.8
    trend_tilt_max: float = 1.2
    trend_sleeve_shift_pct: float = 5.0
    max_sector_pct_of_stock_sleeve: float = 40.0
    stock_min_horizon_years: float = 5.0
    stock_snapshot_ttl_hours: int = 24
    stock_financials_ttl_days: int = 7
    stock_stale_after_days: int = 120
    stock_sync_max_rps: float = 1.5

    # 360° stock report (docs/plans/stock-360-analysis-plan.md)
    stock360_wacc: float = 0.12
    stock360_terminal_growth: float = 0.045
    stock360_terminal_growth_high: float = 0.05
    stock360_buffett_hurdle: float = 0.12
    stock360_buffett_exit_pe_cap: float = 20.0
    stock360_graham_multiplier: float = 22.5
    stock360_cash_conversion_flag: float = 0.70
    stock360_report_ttl_hours: int = 24
    stock360_web_ttl_hours: int = 24
    stock360_max_llm_calls: int = 8
    stock360_max_web_searches: int = 14
    stock360_max_parallel_jobs: int = 2
    stock360_veto_enabled: bool = False
    stock360_veto_min_sell_signals: int = 5
    stock360_web_search_provider: Literal["auto", "openai", "anthropic", "rss", "none"] = "auto"
    tax_equity_stcg_pct: float = 20.0
    tax_equity_ltcg_pct: float = 12.5
    tax_ltcg_exemption_inr: float = 125000.0
    tax_rates_as_of: str = "2024-07-23"

    # Data mode flag: "synthetic" (demo universe) or "real" (curated AMFI / mfapi.in market data)
    data_mode: Literal["synthetic", "real"] = "real"
    # Pluggable market data provider when data_mode="real"
    real_data_provider: Literal["mfapi_amfi", "amfi_only", "yfinance"] = "mfapi_amfi"

    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_model: str = "gpt-6-luna"
    llm_temperature: float = 0.0
    llm_cache_enabled: bool = True
    llm_cache_dir: Path = Path("data/llm_cache")
    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    risk_weights: RiskWeights = RiskWeights()
    analyst_weights: AnalystWeights = AnalystWeights()

    # Risk category boundaries (score < moderate_min is Conservative, >= aggressive_min Aggressive)
    moderate_min: float = 4.0
    aggressive_min: float = 7.0

    # Allocation bands: Profile -> Bucket -> (min_pct, max_pct)
    allocation_bands: dict[str, dict[str, tuple[float, float]]] = {
        "Conservative": {
            "equity": (15.0, 30.0),
            "debt": (70.0, 85.0),
            "gold": (0.0, 10.0),
            "alternatives": (0.0, 0.0),
        },
        "Moderate": {
            "equity": (40.0, 60.0),
            "debt": (30.0, 45.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 5.0),
        },
        "Aggressive": {
            "equity": (70.0, 85.0),
            "debt": (5.0, 20.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 10.0),
        },
    }

    # Minimum sensible holding horizon per category (years)
    category_min_horizons: dict[str, float] = {
        "Liquid": 0.0,
        "Short Duration": 1.0,
        "Corporate Bond": 2.0,
        "Conservative Hybrid": 2.0,
        "Index Fund": 3.0,
        "Large Cap": 3.0,
        "Flexi Cap": 3.0,
        "Mid Cap": 5.0,
        "Gold ETF": 3.0,
        "Small Cap": 7.0,
        "Large & Mid Cap": 5.0,
        "ELSS": 5.0,
        "Aggressive Hybrid": 5.0,
        "Arbitrage": 2.0,
        "Banking & PSU": 1.0,
        "Gilt": 3.0,
    }

    # Allowed categories for fund selection (Set 1 defaults to the original 9)
    allowed_categories: list[str] = [
        "Liquid",
        "Short Duration",
        "Corporate Bond",
        "Conservative Hybrid",
        "Index Fund",
        "Large Cap",
        "Flexi Cap",
        "Mid Cap",
        "Gold ETF",
    ]

    # Master scheme ingestion filters: only store schemes matching allowed plans and options
    master_allowed_plans: list[str] = ["direct"]
    master_allowed_options: list[str] = ["growth"]

    # Capacity caps on equity (percent)
    equity_cap_horizon_lt_1y: float = 5.0
    equity_cap_horizon_lt_3y: float = 20.0
    equity_cap_horizon_lt_5y: float | None = None
    equity_cap_low_emergency: float = 30.0
    low_emergency_months: float = 3.0

    # Suitability & research filters
    min_track_years_equity: float = 5.0
    min_track_years_debt: float = 3.0
    min_aum_cr: float = 500.0
    min_aum_cr_small: float = 100.0  # liquid / gold categories
    min_rating: float | None = 4.0
    require_direct_plan: bool = True
    require_growth_option: bool = True
    require_positive_alpha: bool = False
    max_beta: float | None = None
    min_manager_tenure_years: float | None = None
    match_maturity_to_horizon: bool = False
    ytm_outlier_pp: float | None = None

    # Trusted AMCs and score bonus
    trusted_amcs: list[str] = []
    trusted_amc_bonus: float = 0.0

    # Fundamental sub-score component weights
    fundamental_weights: dict[str, float] = {
        "pct_roll_3y": 0.25,
        "pct_cagr_5y": 0.25,
        "sharpe": 0.25,
        "max_drawdown": 0.25,
        "alpha": 0.0,
        "pct_low_vol": 0.0,
    }

    # Benchmark proxy scheme codes for alpha & beta calculations
    benchmark_proxy_codes: dict[str, str] = {
        "Index": "120716",
        "Index Fund": "120716",
        "Large Cap": "120716",
        "Flexi Cap": "120716",
        "Mid Cap": "147622",
        "Small Cap": "147623",
        "Large & Mid Cap": "120716",
        "ELSS": "120716",
        "Aggressive Hybrid": "120716",
        "equity": "120716",
    }

    # Head-to-head fund comparison weights (Course Notes S3-S19)
    compare_weights: dict[str, float] = {
        "plan_type": 5.0,
        "expense_ratio": 10.0,
        "roll_3y_median": 12.0,
        "rolling_consistency_3y": 8.0,
        "cagr_5y": 8.0,
        "cagr_3y": 5.0,
        "sharpe": 10.0,
        "sortino": 8.0,
        "alpha": 8.0,
        "beta": 4.0,
        "volatility": 6.0,
        "max_drawdown": 7.0,
        "fund_age_years": 4.0,
        "aum_band": 4.0,
        "manager_tenure_years": 6.0,
        "tracking_error": 15.0,
        "ytm": 6.0,
        "modified_duration": 5.0,
    }

    # Position sizing & LLM call bounds
    max_weight_per_instrument: float = 30.0  # percent of whole portfolio
    max_instruments_per_bucket: int = 3
    max_research_picks: int = 3  # max shortlisted funds per bucket in M6
    max_stocks: int = 5  # direct stocks sized in the stock sleeve (default; AI notes may override)
    max_stock_candidates: int = 8  # direct stocks researched before sizing
    max_llm_calls_per_run: int = 12  # hard cap on LLM completions per pipeline run
    # When the AI notes ask for specific funds: budget = base + per_fund * funds, within [default, cap]
    llm_budget_base: int = 2
    llm_budget_per_fund: int = 2
    llm_budget_cap: int = 30
    # AI-notes portfolio spec: fixed equity/debt split and gold slice (taken from debt)
    custom_equity_pct: float = 60.0
    custom_debt_pct: float = 40.0
    custom_gold_pct: float = 5.0
    # Saved web research view (sectors + stocks in the news) used for "trendy" stock picks
    trend_view_path: Path = Path("data/trend_view.json")
    trend_view_stale_days: int = 30
    nse_full_universe_path: Path = Path("data/nse_equity_full.csv")
    llm_max_concurrency: int = 4  # parallel worker threads for analyst LLM calls
    min_ticket: float = 500.0  # rupees; amounts rounded down to this step

    # Drift (5/25 rule)
    drift_abs_pp: float = 5.0
    drift_rel_pct: float = 25.0

    risk_free_rate: float = 0.065

    db_path: Path = Path("data/wealth.db")
    db_path_synthetic: Path = Path("data/wealth_synthetic.db")
    db_path_real: Path = Path("data/wealth_real.db")
    history_db_path: Path = Path("data/wealth_history.db")
    curated_funds_csv: Path = Path("data/curated_funds.csv")
    mfapi_base_url: str = "https://api.mfapi.in/mf"
    nav_history_years: float = 7.0

    def active_db_path(self, mode: str | None = None) -> Path:
        """Resolve the SQLite database path for the given (or configured) data_mode."""
        if "db_path" in self.model_fields_set:
            return self.db_path
        effective = (mode or self.data_mode).lower()
        return self.db_path_real if effective == "real" else self.db_path_synthetic

    # Profile assumptions when the user gives only goal/horizon/risk
    default_savings_rate: float = 0.20
    default_emergency_months: float = 6.0

    default_expense_ceiling: float = 1.0  # percent
    expense_ceilings: dict[str, float] = {
        "Liquid": 0.30,
        "Short Duration": 0.50,
        "Corporate Bond": 0.50,
        "Conservative Hybrid": 0.90,
        "Index Fund": 0.50,
        "Gold ETF": 0.60,
        "Large Cap": 1.00,
        "Flexi Cap": 1.00,
        "Mid Cap": 1.00,
        "Small Cap": 1.00,
        "Large & Mid Cap": 1.00,
        "ELSS": 1.00,
        "Aggressive Hybrid": 1.00,
        "Arbitrage": 0.50,
        "Banking & PSU": 0.50,
        "Gilt": 0.50,
    }
    # Max annualized volatility (fraction) a fund may have for each risk category
    vol_caps: dict[str, float] = {"Conservative": 0.06, "Moderate": 0.14, "Aggressive": 0.25}

    # RAG
    doc_max_age_days: int = 365
    embedding_dim: int = 512

    # Decision & Daily Recalibration (M8, M10, M11)
    buy_min_score: float = 50.0
    min_confidence: float = 0.6
    rebalance_buy_min_score: float = 70.0
    rebalance_hold_min_score: float = 50.0
    rebalance_decrease_min_score: float = 35.0
    conviction_delta_min: float = 10.0
    rebalance_cooldown_days: int = 7
    daily_schedule_hour_ist: int = 8
    daily_schedule_minute_ist: int = 0
    exit_load_holding_days: int = 365


@lru_cache
def _base_settings() -> Settings:
    return Settings()


_settings_by_set: dict[str, Settings] = {}


def get_settings(rule_set: str | None = None) -> Settings:
    """Return Settings for the requested, active, or configured rule set."""
    from .domain.rulesets import active_rule_set, get_rule_set_overrides

    base = _base_settings()
    set_name = (rule_set or active_rule_set() or base.fund_rule_set).lower()
    if set_name in _settings_by_set:
        return _settings_by_set[set_name]

    overrides = get_rule_set_overrides(set_name)
    if not overrides:
        s = base
    else:
        update_data = dict(overrides)
        if "analyst_weights" in update_data and isinstance(update_data["analyst_weights"], dict):
            update_data["analyst_weights"] = AnalystWeights(**update_data["analyst_weights"])
        s = base.model_copy(update=update_data, deep=True)

    _settings_by_set[set_name] = s
    return s


def cache_clear() -> None:
    _base_settings.cache_clear()
    _settings_by_set.clear()


get_settings.cache_clear = cache_clear  # type: ignore[attr-defined]
