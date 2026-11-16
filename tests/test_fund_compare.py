from datetime import date

from wealth_advisor.data.on_demand import FundProfile
from wealth_advisor.domain.fund_compare import build_checklist, compare_funds
from wealth_advisor.models import Bucket


def _make_profile(
    code: str,
    *,
    name: str = "Sample Fund",
    category: str = "Large Cap",
    bucket: Bucket = Bucket.EQUITY,
    cagr_3y: float | None = 15.0,
    cagr_5y: float | None = 16.0,
    roll_3y_median: float | None = 15.5,
    rolling_consistency_3y: float | None = 90.0,
    sharpe: float | None = 1.25,
    sortino: float | None = 1.65,
    alpha: float | None = 2.0,
    beta: float | None = 0.92,
    volatility: float | None = 13.5,
    max_drawdown: float | None = -12.0,
    expense_ratio: float | None = 0.55,
    aum_cr: float | None = 8500.0,
    fund_age_years: float | None = 8.0,
    manager_tenure_years: float | None = 5.0,
    tracking_error: float | None = None,
    ytm: float | None = None,
    avg_maturity_years: float | None = None,
    modified_duration: float | None = None,
) -> FundProfile:
    return FundProfile(
        code=code,
        name=name,
        amc="HDFC Mutual Fund",
        category=category,
        sebi_category=category,
        bucket=bucket,
        plan="direct",
        option="growth",
        source="test",
        as_of=date(2026, 10, 15),
        cagr_1y=12.0,
        cagr_3y=cagr_3y,
        cagr_5y=cagr_5y,
        roll_3y_median=roll_3y_median,
        rolling_consistency_3y=rolling_consistency_3y,
        sharpe=sharpe,
        sortino=sortino,
        alpha=alpha,
        beta=beta,
        volatility=volatility,
        max_drawdown=max_drawdown,
        expense_ratio=expense_ratio,
        aum_cr=aum_cr,
        fund_age_years=fund_age_years,
        manager_tenure_years=manager_tenure_years,
        tracking_error=tracking_error,
        ytm=ytm,
        avg_maturity_years=avg_maturity_years,
        modified_duration=modified_duration,
    )


def test_swap_symmetry_preserves_scores_and_mirrors_winners():
    pa = _make_profile("FUND_A", cagr_5y=18.0, sharpe=1.4, expense_ratio=0.45, max_drawdown=-10.0)
    pb = _make_profile("FUND_B", cagr_5y=13.0, sharpe=1.0, expense_ratio=0.85, max_drawdown=-18.0)

    res_ab = compare_funds(pa, pb)
    res_ba = compare_funds(pb, pa)

    assert abs(res_ab.scores["FUND_A"] - res_ba.scores["FUND_A"]) < 1e-6
    assert abs(res_ab.scores["FUND_B"] - res_ba.scores["FUND_B"]) < 1e-6
    assert res_ab.ranking == res_ba.ranking == ["FUND_A", "FUND_B"]

    for c_ab, c_ba in zip(res_ab.criteria, res_ba.criteria):
        assert c_ab.id == c_ba.id
        if c_ab.winner == "A":
            assert c_ba.winner == "B"
        elif c_ab.winner == "B":
            assert c_ba.winner == "A"
        else:
            assert c_ba.winner == c_ab.winner


def test_self_comparison_is_exact_tie():
    pa = _make_profile("FUND_A")
    pa_clone = _make_profile("FUND_B")

    res = compare_funds(pa, pa_clone)
    assert res.scores["FUND_A"] == 50.0
    assert res.scores["FUND_B"] == 50.0
    assert res.winner == "tie"
    for c in res.criteria:
        if c.status == "scored":
            assert c.winner == "tie"


def test_no_data_weight_rescaling():
    # Missing 5Y CAGR and AUM on Fund B -> those criteria become no_data and remaining weights rescale to 100
    pa = _make_profile("FUND_A", cagr_5y=15.0, aum_cr=5000.0)
    pb = _make_profile("FUND_B", cagr_5y=None, aum_cr=None)

    res = compare_funds(pa, pb)
    by_id = {c.id: c for c in res.criteria}
    assert by_id["cagr_5y"].status == "no_data"
    assert by_id["aum_band"].status == "no_data"

    # Scores on remaining scored criteria still sum to 100.0
    total = res.scores["FUND_A"] + res.scores["FUND_B"]
    assert abs(total - 100.0) < 0.2


def test_cross_category_comparison_warns_and_scores_only_category_neutral():
    eq = _make_profile("EQ1", category="Large Cap", bucket=Bucket.EQUITY)
    dt = _make_profile(
        "DT1",
        category="Short Duration",
        bucket=Bucket.DEBT,
        ytm=7.4,
        avg_maturity_years=2.5,
        modified_duration=2.1,
    )

    res = compare_funds(eq, dt)
    assert res.same_category is False
    assert any("Notes S7: compare within the same category" in w for w in res.warnings)

    by_id = {c.id: c for c in res.criteria}
    # Category-specific criteria should be not_applicable in cross-category comparison
    assert by_id["cagr_5y"].status == "not_applicable"
    assert by_id["cagr_3y"].status == "not_applicable"
    assert by_id["expense_ratio"].status == "not_applicable"
    # Category-neutral criteria (sharpe, alpha, max_drawdown, etc.) remain scored
    assert by_id["sharpe"].status == "scored"
    assert by_id["max_drawdown"].status == "scored"


def test_set2_checklist_thresholds_flag_violations():
    good = _make_profile(
        "GOOD",
        expense_ratio=0.50,
        rolling_consistency_3y=88.0,
        sharpe=1.30,
        alpha=1.5,
        aum_cr=5000.0,
        fund_age_years=8.0,
        manager_tenure_years=5.0,
    )
    good_flags = {f.id: f for f in build_checklist(good)}
    assert not any(f.status == "flag" for f in good_flags.values())

    bad = _make_profile(
        "BAD",
        expense_ratio=1.45,            # > 1.0% ceiling
        alpha=-0.8,                    # < 0 threshold
        aum_cr=250.0,                  # < 500 Cr sweet spot
        fund_age_years=2.5,            # < 5y track record
        manager_tenure_years=1.5,      # < 3y threshold
    )
    bad_flags = {f.id: f for f in build_checklist(bad)}
    assert bad_flags["ter_ceiling"].status == "flag"
    assert bad_flags["track_record"].status == "flag"
    assert bad_flags["alpha_positive"].status == "flag"
    assert bad_flags["aum_sweet_spot"].status == "flag"
    assert bad_flags["manager_tenure"].status == "flag"


def test_compare_funds_is_deterministic():
    pa = _make_profile("FUND_A", cagr_5y=17.2, sharpe=1.35, expense_ratio=0.42)
    pb = _make_profile("FUND_B", cagr_5y=14.8, sharpe=1.12, expense_ratio=0.68)

    r1 = compare_funds(pa, pb).model_dump()
    r2 = compare_funds(pa, pb).model_dump()
    assert r1 == r2


def test_compute_fund_profile_for_master_only_scheme():
    import pandas as pd
    from wealth_advisor.data.on_demand import compute_fund_profile
    from wealth_advisor.data.store import MetadataStore, SchemeMasterRecord
    from wealth_advisor.db import open_db

    conn = open_db(":memory:")
    store = MetadataStore(conn)
    store.upsert_master(
        [
            SchemeMasterRecord(
                code="120828",
                name="Quant Small Cap Fund - Direct Plan - Growth",
                amc="quant Mutual Fund",
                sebi_category="Equity Scheme - Small Cap Fund",
                category="Small Cap",
                plan="direct",
                option="growth",
                as_of=date(2026, 10, 15),
            )
        ]
    )
    dates = pd.bdate_range("2020-01-01", "2026-10-15")
    nav = pd.Series([100.0 * (1.0005 ** i) for i in range(len(dates))], index=dates)
    store.save_nav("120828", nav)

    prof = compute_fund_profile(store, "120828", fetch_if_missing=False)
    assert prof.code == "120828"
    assert prof.category == "Small Cap"
    assert prof.benchmark == "Nifty Smallcap 250 TRI"
    assert prof.cagr_5y is not None
    conn.close()


def test_ensure_nav_fetches_from_mfapi_and_computes_metrics(monkeypatch):
    import pandas as pd
    import wealth_advisor.data.on_demand as on_demand_mod
    from wealth_advisor.data.on_demand import compute_fund_profile
    from wealth_advisor.data.store import MetadataStore, SchemeMasterRecord
    from wealth_advisor.db import open_db

    conn = open_db(":memory:")
    store = MetadataStore(conn)
    store.upsert_master(
        [
            SchemeMasterRecord(
                code="120828",
                name="Quant Small Cap Fund - Direct Plan - Growth",
                amc="",
                sebi_category="Equity Scheme - Small Cap Fund",
                category="Small Cap",
                plan="direct",
                option="growth",
                as_of=date(2026, 10, 15),
            ),
            SchemeMasterRecord(
                code="147623",
                name="Motilal Oswal Nifty Smallcap 250 Index Fund - Direct Plan Growth",
                amc="Motilal Oswal Mutual Fund",
                sebi_category="Other Scheme - Index Funds",
                category="Index",
                plan="direct",
                option="growth",
                as_of=date(2026, 10, 15),
            ),
        ]
    )

    dates = pd.bdate_range("2020-01-01", "2026-10-15")

    def _fake_fetch_mfapi_json(code: str, *args, **kwargs) -> dict:
        growth = 1.0006 if code == "120828" else 1.0004
        return {
            "meta": {
                "fund_house": "quant Mutual Fund" if code == "120828" else "Motilal Oswal Mutual Fund",
                "scheme_name": f"Scheme {code}",
                "isin_growth": f"INF000{code}",
            },
            "data": [
                {"date": dt.strftime("%d-%m-%Y"), "nav": f"{100.0 * (growth ** i):.4f}"}
                for i, dt in enumerate(dates)
            ],
        }

    monkeypatch.setattr(on_demand_mod, "fetch_mfapi_json", _fake_fetch_mfapi_json)

    prof = compute_fund_profile(store, "120828", fetch_if_missing=True)
    assert prof.cagr_5y is not None
    assert prof.cagr_3y is not None
    assert prof.roll_3y_median is not None
    assert prof.rolling_consistency_3y is not None
    assert prof.sharpe is not None
    assert prof.sortino is not None
    assert prof.alpha is not None
    assert prof.beta is not None
    assert prof.volatility is not None
    assert prof.max_drawdown is not None
    assert prof.fund_age_years is not None
    assert prof.amc == "quant Mutual Fund"
    conn.close()


