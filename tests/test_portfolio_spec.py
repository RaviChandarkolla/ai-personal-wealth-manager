"""AI-notes portfolio spec: parsing, deterministic targets, rule overrides, selection and pipeline."""

from datetime import date

import pytest

from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM
from wealth_advisor.agents.profiling import ExtractedGoal, ProfilingAgent, RawFundRequest
from wealth_advisor.audit.log import AuditLog
from wealth_advisor.audit.replay import replay
from wealth_advisor.config import Settings
from wealth_advisor.data.trend_view import (
    DraftSector,
    DraftStock,
    Headline,
    TrendDraft,
    TrendSector,
    TrendSource,
    TrendStock,
    TrendView,
    refresh_trend_view,
    trend_view_status,
    validate_draft,
)
from wealth_advisor.domain.portfolio_spec import (
    build_spec,
    custom_target,
    describe_spec,
    effective_settings,
    llm_budget_for,
    map_category,
    stock_sleeve_weight,
)
from wealth_advisor.guardrails.suitability import check_fund, overridden_violations
from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import Bucket, RiskPreference
from wealth_advisor.pipeline import advise

S = Settings()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("liquid", "Liquid"),
        ("Flexi", "Flexi Cap"),
        ("flexi cap fund", "Flexi Cap"),
        ("small-cap", "Small Cap"),
        ("mid cap", "Mid Cap"),
        ("large cap", "Large Cap"),
        ("large & mid cap", "Large & Mid Cap"),
        ("debt", "Debt"),
        ("short term bond", "Short Duration"),
        ("gold", "Gold ETF"),
        ("banking and psu debt", "Banking & PSU"),
        ("crypto", None),
    ],
)
def test_map_category(text, expected):
    assert map_category(text) == expected


def test_build_spec_merges_validates_and_warns():
    spec = build_spec([("liquid", 1), ("debt", 1), ("flexi", 2), ("flexi cap", 1), ("crypto", 1)], 10, 14, None)
    assert spec is not None
    got = {r.category: r.count for r in spec.fund_requests}
    assert got == {"Liquid": 1, "Debt": 1, "Flexi Cap": 3}
    assert spec.stock_count == 10 and spec.trendy_stock_count == 10  # trendy cannot exceed stocks
    assert any("crypto" in w for w in spec.warnings)
    assert any("trendy" in w for w in spec.warnings)
    assert spec.is_custom and spec.all_stocks_from_web


def test_build_spec_none_when_nothing_requested():
    assert build_spec([], None, None, None) is None


def test_gold_only_request_is_not_custom():
    spec = build_spec([], None, None, True)
    assert spec is not None and not spec.is_custom


def test_trendy_without_stock_count_sets_stock_count():
    spec = build_spec([], None, 4, None)
    assert spec is not None and spec.stock_count == 4 and spec.trendy_stock_count == 4


def _spec(funds, stocks=None, trendy=None, gold=None):
    spec = build_spec(funds, stocks, trendy, gold)
    assert spec is not None
    return spec


def test_custom_target_60_40_and_gold_from_debt():
    both = custom_target(_spec([("flexi", 1), ("liquid", 1)], 5), S)
    assert both[Bucket.EQUITY] == 60.0 and both[Bucket.DEBT] == 40.0 and both[Bucket.GOLD] == 0.0
    gold = custom_target(_spec([("flexi", 1), ("liquid", 1), ("gold", 1)]), S)
    assert gold[Bucket.EQUITY] == 60.0 and gold[Bucket.DEBT] == 35.0 and gold[Bucket.GOLD] == 5.0
    assert sum(gold.values()) == pytest.approx(100.0)


def test_custom_target_only_x_wins():
    assert custom_target(_spec([], 10), S)[Bucket.EQUITY] == 100.0
    assert custom_target(_spec([("liquid", 1), ("debt", 1)]), S)[Bucket.DEBT] == 100.0
    eq_gold = custom_target(_spec([("flexi", 1), ("gold", 1)]), S)
    assert eq_gold[Bucket.EQUITY] == 95.0 and eq_gold[Bucket.GOLD] == 5.0 and eq_gold[Bucket.DEBT] == 0.0


def test_stock_sleeve_is_half_of_equity_when_equity_funds_exist():
    assert stock_sleeve_weight(_spec([("flexi", 1), ("liquid", 1)], 10), 60.0) == 30.0
    assert stock_sleeve_weight(_spec([("liquid", 1)], 10), 60.0) == 60.0
    assert stock_sleeve_weight(_spec([("flexi", 1)]), 60.0) == 0.0


def test_effective_settings_defaults_kept_without_spec():
    assert effective_settings(S, None) is S
    gold_only = build_spec([], None, None, True)
    assert effective_settings(S, gold_only) is S
    custom = effective_settings(S, _spec([("flexi", 2), ("small cap", 2)], 10))
    assert custom.max_stocks == 10 and custom.max_instruments_per_bucket == 4
    assert custom.max_weight_per_stock == 100.0 and S.max_stocks == 5  # original untouched


def test_llm_budget_scales_with_requested_funds():
    assert llm_budget_for(None, S) == 12
    six = _spec([("liquid", 1), ("debt", 1), ("flexi", 2), ("small cap", 2)], 10, 4)
    assert llm_budget_for(six, S) == 14
    huge = _spec([("flexi", 10), ("small cap", 10), ("debt", 10)])
    assert llm_budget_for(huge, S) == S.llm_budget_cap
    assert describe_spec(six)[-1] == "10 direct stocks (4 from the web trend view)"


def test_notes_waive_context_rules_but_not_data_rules():
    facts = {"plan": "direct", "expense_ratio": 0.8, "aum_cr": 5000.0, "track_years": 10.0, "rating": 5.0}
    plain = Goal(amount=500000, horizon_years=5, risk_preference=RiskPreference.MODERATE)
    asked = plain.model_copy(update={"spec": _spec([("small cap", 2)])})
    assert any(v.rule == "horizon" for v in check_fund("X", "Small Cap", facts, plain, S))
    assert check_fund("X", "Small Cap", facts, asked, S) == []
    waived = {v.rule for v in overridden_violations("X", "Small Cap", facts, asked, S)}
    assert waived == {"horizon", "allowed_category"}  # Small Cap is outside the default rule set too
    regular = {**facts, "plan": "regular"}
    assert any(v.rule == "plan" for v in check_fund("X", "Small Cap", regular, asked, S))
    pricey = {**facts, "expense_ratio": 2.5}
    assert any(v.rule == "expense_ratio" for v in check_fund("X", "Small Cap", pricey, asked, S))


def test_profiling_builds_spec_from_extracted_notes():
    llm = ScriptedExtract(
        ExtractedGoal(
            purpose="growth",
            fund_requests=[RawFundRequest(category="flexi", count=2), RawFundRequest(category="liquid", count=1)],
            stock_count=10,
            trendy_stock_count=4,
        )
    )
    res = ProfilingAgent(llm).resolve(
        "2 flexi, 1 liquid and 10 stocks, 4 trendy", amount=500000, horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    assert res.goal is not None and res.goal.spec is not None
    assert res.goal.spec.stock_count == 10 and res.goal.spec.total_funds == 3
    assert "holding 2 × Flexi Cap funds" in ProfilingAgent.restate(res.goal)


class ScriptedExtract:
    model = "scripted"

    def __init__(self, extracted):
        self._extracted = extracted

    def complete(self, messages, schema=None):
        return self._extracted


# ----- web trend view -----

HEADLINES = [Headline(id=f"H{i}", title=f"Headline {i}", url=f"https://n/{i}", publisher="Pub") for i in range(1, 13)]
SYMBOLS = {"BEL": ("Bharat Electronics Limited", "Capital Goods"), "TCS": ("TCS", "Information Technology"),
           "HAL": ("Hindustan Aeronautics Limited", "Capital Goods")}


def test_validate_draft_drops_invented_and_uncited_entries():
    draft = TrendDraft(
        sectors=[
            DraftSector(name="Defence", outlook="positive", score=80, rationale="orders", headline_ids=["H1", "H2"]),
            DraftSector(name="Ghost", outlook="positive", score=90, rationale="none", headline_ids=["H99"]),
        ],
        stocks=[
            DraftStock(symbol="bel", sector="Defence", conviction=100, thesis="t", headline_ids=["H1", "H2"]),
            DraftStock(symbol="HAL", sector="Defence", conviction=100, thesis="t", headline_ids=["H3"]),
            DraftStock(symbol="FAKECO", sector="Defence", conviction=100, thesis="t", headline_ids=["H1"]),
            DraftStock(symbol="TCS", sector="IT", conviction=100, thesis="t", headline_ids=["H99"]),
        ],
    )
    view = validate_draft(draft, HEADLINES, SYMBOLS, as_of=date(2026, 10, 22), generated_by="test")
    assert [s.name for s in view.sectors] == ["Defence"]
    by_sym = {s.symbol: s for s in view.stocks}
    assert set(by_sym) == {"BEL", "HAL"}
    assert by_sym["BEL"].score == 0.6 * 80 + 0.4 * 100
    assert by_sym["HAL"].score == 0.6 * 80 + 0.4 * 70  # one headline caps conviction
    assert view.stocks[0].symbol == "BEL"


def test_refresh_trend_view_saves_and_keeps_old_view_on_failure(tmp_path):
    s = Settings(trend_view_path=tmp_path / "view.json", nse_full_universe_path=tmp_path / "none.csv")
    draft = TrendDraft(
        sectors=[DraftSector(name="Capital Goods", outlook="positive", score=70, rationale="r", headline_ids=["H1"])],
        stocks=[
            DraftStock(symbol=sym, sector="Capital Goods", conviction=70, thesis="t", headline_ids=["H1", "H2"])
            for sym in ("TCS", "INFY", "HCLTECH")
        ],
    )

    class Fixed:
        model = "fixed"

        def complete(self, messages, schema=None):
            return draft

    view = refresh_trend_view(Fixed(), s, fetch=lambda: HEADLINES, today=date(2026, 10, 22))
    assert len(view.stocks) == 3 and (tmp_path / "view.json").exists()
    with pytest.raises(ValueError):
        refresh_trend_view(Fixed(), s, fetch=lambda: HEADLINES[:3])
    assert trend_view_status(view, s, today=date(2026, 10, 23))["stale"] is False
    assert trend_view_status(view, s, today=date(2026, 12, 30))["stale"] is True
    assert trend_view_status(None, s)["available"] is False


# ----- end to end -----

def _view(symbols):
    src = [TrendSource(title="Defence orders surge", url="https://n/1", publisher="Pub")]
    return TrendView(
        as_of=date.today(), generated_by="test",
        sectors=[TrendSector(name="Capital Goods", outlook="positive", score=80, rationale="r", sources=src)],
        stocks=[
            TrendStock(symbol=sym, name=f"{sym} Corp", sector="Capital Goods", score=90 - i, thesis="t", sources=src)
            for i, sym in enumerate(symbols)
        ],
    )


def test_pipeline_honours_notes(demo_conn, monkeypatch):
    monkeypatch.setattr(
        "wealth_advisor.pipeline.load_trend_view", lambda *_a, **_k: _view(["BEL", "HAL", "RVNL", "IRFC"])
    )
    spec = _spec([("liquid", 1), ("debt", 1), ("flexi", 2), ("small cap", 2)], 10, 4)
    goal = Goal(amount=1_000_000, horizon_years=5, risk_preference=RiskPreference.MODERATE, spec=spec)
    llm = ScriptedLLM()
    res = advise(goal, llm, demo_conn, as_of=AS_OF)

    rows = res.decision.rows
    funds = [r for r in rows if r.instrument_type != "stock"]
    stocks = [r for r in rows if r.instrument_type == "stock"]
    cats = sorted(r.category for r in funds)
    assert cats.count("Flexi Cap") == 2 and cats.count("Liquid") == 1
    assert len([r for r in funds if r.bucket == Bucket.DEBT]) == 2  # liquid + best debt fund
    assert "Gold ETF" not in cats and not any(r.bucket == Bucket.GOLD for r in rows)
    assert len(stocks) == 10
    assert {"BEL", "HAL", "RVNL", "IRFC"} <= {r.instrument_id for r in stocks}

    eq = sum(r.weight for r in rows if r.bucket == Bucket.EQUITY)
    debt = sum(r.weight for r in rows if r.bucket == Bucket.DEBT)
    assert eq == pytest.approx(60.0, abs=0.01) and debt == pytest.approx(40.0, abs=0.01)
    stock_w = sum(r.weight for r in stocks)
    assert stock_w == pytest.approx(30.0, abs=0.01)  # half of the 60% equity

    rep = res.spec_report
    assert any("Small Cap" in m for m in rep["shortfalls"])  # demo data has no small-cap funds
    assert any("60%" in m for m in rep["overrides"])
    assert "BEL" in rep["web_trend_symbols"]
    assert llm.calls <= 14
    assert any("not fundamentally screened" in d for d in res.compliance.disclosures)

    ctx = res.build_context()
    assert ctx["portfolio_spec"]["stock_count"] == 10
    assert ctx["sizing_limits"]["max_instruments_per_bucket"] >= 2

    audit = AuditLog(demo_conn)
    report = replay(audit, res.run_id)
    assert report.ok, [c for c in report.checks if not c.ok]


def test_pipeline_defaults_unchanged_without_spec(demo_conn):
    goal = Goal(amount=500000, horizon_years=7, risk_preference=RiskPreference.MODERATE)
    res = advise(goal, ScriptedLLM(), demo_conn, as_of=AS_OF)
    assert res.portfolio_spec is None and res.spec_report == {}
    assert not any(r.instrument_type == "stock" for r in res.decision.rows)
    assert len([r for r in res.decision.rows if r.bucket == Bucket.EQUITY]) <= S.max_instruments_per_bucket


def test_stocks_only_notes_put_everything_in_stocks(demo_conn, monkeypatch):
    monkeypatch.setattr("wealth_advisor.pipeline.load_trend_view", lambda *_a, **_k: _view(["BEL", "HAL", "RVNL"]))
    spec = _spec([], 3, 3)
    goal = Goal(amount=300000, horizon_years=2, risk_preference=RiskPreference.CONSERVATIVE, spec=spec)
    res = advise(goal, ScriptedLLM(), demo_conn, as_of=AS_OF)
    rows = res.decision.rows
    assert {r.instrument_id for r in rows} == {"BEL", "HAL", "RVNL"}
    assert sum(r.weight for r in rows) == pytest.approx(100.0, abs=0.01)
    assert any("horizon" in m for m in res.spec_report["overrides"]) or res.spec_report["overrides"]


def test_missing_trend_view_falls_back_with_warning(demo_conn, monkeypatch):
    monkeypatch.setattr("wealth_advisor.pipeline.load_trend_view", lambda *_a, **_k: None)
    spec = _spec([("flexi", 1)], 4, 2)
    goal = Goal(amount=500000, horizon_years=5, risk_preference=RiskPreference.MODERATE, spec=spec)
    res = advise(goal, ScriptedLLM(), demo_conn, as_of=AS_OF)
    assert any("trend view" in w.lower() for w in res.spec_report["warnings"])
    assert len([r for r in res.decision.rows if r.instrument_type == "stock"]) >= 1


# ----- API -----

class NotesLLM(ScriptedLLM):
    """Scripted LLM whose note parsing returns a fixed portfolio request."""

    def complete(self, messages, schema=None):
        if schema is not None and schema.__name__ == "ExtractedGoal":
            self.calls += 1
            return ExtractedGoal(
                purpose="growth",
                fund_requests=[RawFundRequest(category="flexi", count=2), RawFundRequest(category="liquid", count=1)],
                stock_count=4,
                trendy_stock_count=2,
            )
        return super().complete(messages, schema)


@pytest.fixture
def notes_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from wealth_advisor.api import main as api
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.db import open_db

    db_path = tmp_path / "notes.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()
    settings = Settings(
        data_mode="synthetic", db_path_synthetic=db_path, db_path_real=db_path,
        history_db_path=tmp_path / "history.db", trend_view_path=tmp_path / "view.json",
        nse_full_universe_path=tmp_path / "none.csv",
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_llm", lambda: NotesLLM())
    monkeypatch.setattr("wealth_advisor.pipeline.load_trend_view", lambda *_a, **_k: _view(["BEL", "HAL"]))
    return TestClient(api.app), api


def test_preview_and_advise_with_confirmed_spec(notes_client):
    client, _ = notes_client
    body = {"query": "2 flexi, 1 liquid, 4 stocks of which 2 trendy", "amount": 500000,
            "horizon_years": 5, "risk_preference": "moderate", "data_mode": "synthetic"}
    pv = client.post("/api/advise/preview", json=body)
    assert pv.status_code == 200
    data = pv.json()
    assert data["custom"] is True
    assert data["understood"] == ["2 × Flexi Cap funds", "1 × Liquid fund", "4 direct stocks (2 from the web trend view)"]
    assert data["llm_calls_budget"] == 12

    r = client.post("/api/advise", json={**body, "portfolio_spec": data["portfolio_spec"]})
    assert r.status_code == 200, r.text
    resp = r.json()
    assert resp["portfolio_spec"]["stock_count"] == 4
    assert resp["spec_report"]["requested"][0] == "2 × Flexi Cap funds"
    assert sum(1 for x in resp["recommendations"] if x["instrument_type"] == "stock") == 4
    assert {"BEL", "HAL"} <= {x["instrument_id"] for x in resp["recommendations"]}


def test_trend_view_endpoints(notes_client, monkeypatch):
    from wealth_advisor.data import trend_view as tv

    client, api = notes_client
    empty = client.get("/api/trend-view").json()
    assert empty["status"]["available"] is False and empty["view"] is None

    draft = TrendDraft(
        sectors=[DraftSector(name="Capital Goods", outlook="positive", score=70, rationale="r", headline_ids=["H1"])],
        stocks=[DraftStock(symbol=s, sector="Capital Goods", conviction=70, thesis="t", headline_ids=["H1", "H2"])
                for s in ("TCS", "INFY", "HCLTECH")],
    )

    class Fixed:
        model = "fixed"

        def complete(self, messages, schema=None):
            return draft

    monkeypatch.setattr(api, "get_llm", lambda: Fixed())
    monkeypatch.setattr(tv, "fetch_headlines", lambda *a, **k: HEADLINES)
    monkeypatch.setattr(tv, "refresh_trend_view", _wrap_refresh(tv.refresh_trend_view))
    ok = client.post("/api/trend-view/refresh")
    assert ok.status_code == 200 and ok.json()["status"]["stock_count"] == 3
    assert client.get("/api/trend-view").json()["status"]["available"] is True


def _wrap_refresh(real):
    def inner(llm, s=None, **kw):
        return real(llm, s, fetch=lambda: HEADLINES, **kw)

    return inner
