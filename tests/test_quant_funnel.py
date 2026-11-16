from datetime import date

import pandas as pd

from wealth_advisor.agents.mf_researcher import (
    MFResearchAgent,
    Pick,
    ResearcherConfig,
    Selection,
    _quant_rank_score,
)
from wealth_advisor.agents.tools import ResearchTools
from wealth_advisor.config import get_settings
from wealth_advisor.data.stats import compute_all_stats
from wealth_advisor.data.store import MetadataStore, SchemeMasterRecord
from wealth_advisor.db import open_db
from wealth_advisor.llm.fake import FakeLLM
from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import Bucket, RiskPreference
from wealth_advisor.rag.index import VectorIndex


def _seed_master_fund(
    store: MetadataStore,
    code: str,
    name: str,
    category: str,
    *,
    ter: float,
    aum_cr: float,
    start_nav: float = 10.0,
    end_nav: float = 20.0,
    seed_nav: bool = True,
) -> None:
    store.upsert_master(
        [
            SchemeMasterRecord(
                code=code,
                isin_growth=f"INF{code.zfill(9)}",
                name=name,
                amc="Test AMC",
                sebi_category=f"Equity Scheme - {category}",
                category=category,
                plan="direct",
                option="growth",
                structure="open",
                latest_nav=end_nav,
                nav_date=date(2026, 10, 9),
                expense_ratio=ter,
                ter_as_of=date(2026, 10, 1),
                aum_cr=aum_cr,
                aum_as_of=date(2026, 9, 30),
                source="AMFI_MASTER",
                as_of=date(2026, 10, 9),
            )
        ]
    )
    if seed_nav:
        dates = pd.bdate_range("2020-01-01", "2026-10-09")
        vals = [
            start_nav + (end_nav - start_nav) * (i / max(1, len(dates) - 1))
            for i in range(len(dates))
        ]
        store.save_nav(code, pd.Series(vals, index=dates))


def test_eligible_master_funds_prioritizes_hygiene_and_aum():
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    _seed_master_fund(
        store, "M101", "High TER Flexi Direct Growth", "Flexi Cap", ter=2.50, aum_cr=9000.0, seed_nav=False
    )
    _seed_master_fund(
        store, "M102", "Institutional Flexi Direct Growth", "Flexi Cap", ter=0.45, aum_cr=5000.0, seed_nav=False
    )
    _seed_master_fund(
        store, "M103", "Top AUM Flexi Direct Growth", "Flexi Cap", ter=0.55, aum_cr=12000.0, seed_nav=False
    )

    eligible = store.eligible_master_funds(categories=["Flexi Cap"], per_category_limit=2, only_unseeded=True)
    assert [f.code for f in eligible] == ["M103", "M102"]


def test_mf_researcher_three_stage_funnel_filters_and_ranks():
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    index = VectorIndex(conn)
    as_of = date(2026, 10, 9)

    # Seed 4 suitable Flexi Cap funds and 1 unsuitable (high TER) Flexi Cap fund in scheme_master
    _seed_master_fund(store, "F001", "Alpha Flexi Cap Direct Growth", "Flexi Cap", ter=0.40, aum_cr=8000.0, start_nav=10.0, end_nav=26.0)
    _seed_master_fund(store, "F002", "Beta Flexi Cap Direct Growth", "Flexi Cap", ter=0.50, aum_cr=6000.0, start_nav=10.0, end_nav=24.0)
    _seed_master_fund(store, "F003", "Gamma Flexi Cap Direct Growth", "Flexi Cap", ter=0.60, aum_cr=4000.0, start_nav=10.0, end_nav=22.0)
    _seed_master_fund(store, "F004", "Delta Flexi Cap Direct Growth", "Flexi Cap", ter=0.85, aum_cr=1500.0, start_nav=10.0, end_nav=16.0)
    _seed_master_fund(store, "F_BAD", "Expensive Flexi Cap Direct Growth", "Flexi Cap", ter=2.40, aum_cr=5000.0, start_nav=10.0, end_nav=25.0)
    compute_all_stats(store, as_of=as_of)

    cfg = ResearcherConfig(
        asset_class=Bucket.EQUITY,
        persona="Equity researcher",
        tools=ResearchTools.ALL,
        output_field="mf",
        categories=("Flexi Cap",),
        max_picks=2,
        top_per_category=3,
    )
    llm = FakeLLM([Selection(picks=[Pick(scheme_code="F001", reason=""), Pick(scheme_code="F002", reason="")])])
    agent = MFResearchAgent(llm, ResearchTools(store, index, as_of=as_of), cfg)
    goal = Goal(amount=500_000, horizon_years=7, risk_preference=RiskPreference.GROWTH)

    res = agent.run(goal, as_of)
    assert res.funnel_stats["universe_count"] == 5
    assert res.funnel_stats["suitable_count"] == 4
    assert res.funnel_stats["shortlisted_count"] == 3
    assert res.funnel_stats["picked_count"] == 2

    prompt_table = agent.last_prompts[0][1]["content"]
    assert "F_BAD" not in prompt_table
    assert "F004" not in prompt_table
    assert "F001" in prompt_table
    assert "F002" in prompt_table
    assert "F003" in prompt_table
    assert _quant_rank_score(store.find_fund("F001"), {"expense_ratio": 0.4, "aum_cr": 8000.0, "pct_roll_3y": 90.0, "pct_cagr_5y": 90.0}, get_settings()) > 0
