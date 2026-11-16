import logging
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Depends, Query, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from wealth_advisor.agents.followup import FollowUpAgent
from wealth_advisor.agents.stock_360_analyst import REPORT_DISCLOSURE
from wealth_advisor.bootstrap import bootstrap_demo, bootstrap_real
from wealth_advisor.config import get_settings
from wealth_advisor.history import HistoryStore, build_run_title
from wealth_advisor.logging_setup import new_run_id, setup_logging
from wealth_advisor.llm import flush_langfuse, trace_context
from wealth_advisor.llm.base import BudgetedLLM
from wealth_advisor.llm.factory import get_llm
from wealth_advisor.models.core import PortfolioSpec
from wealth_advisor.db import open_db
from wealth_advisor.pipeline import advise
from wealth_advisor.guardrails.suitability import SuitabilityError
from wealth_advisor.data.news import get_fund_news, get_macro_and_regulatory_news
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.domain.rulesets import (
    AVAILABLE_RULE_SETS,
    active_rule_set,
    get_default_rule_set,
    set_active_rule_set,
)
from wealth_advisor.domain.rule_catalog import describe_rule_set, compare_rule_sets

log = logging.getLogger(__name__)

app = FastAPI(title="Wealth Advisor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_runtime_data_mode: Optional[Literal["synthetic", "real"]] = None


def get_active_mode() -> Literal["synthetic", "real"]:
    if _runtime_data_mode in ("synthetic", "real"):
        return _runtime_data_mode
    return get_settings().data_mode


def set_active_mode(mode: Literal["synthetic", "real"], persist_env: bool = False) -> None:
    global _runtime_data_mode
    _runtime_data_mode = mode
    if persist_env:
        cfg_path = Path(__file__).resolve().parent.parent / "config.py"
        if cfg_path.exists():
            try:
                content = cfg_path.read_text()
                updated = re.sub(
                    r'(data_mode:\s*Literal\["synthetic",\s*"real"\]\s*=\s*)"(?:synthetic|real)"',
                    rf'\1"{mode}"',
                    content,
                    count=1,
                )
                if updated != content:
                    cfg_path.write_text(updated)
                    get_settings.cache_clear()
            except OSError as exc:
                log.warning("Could not persist data_mode to config.py: %s", exc)


def set_rule_set_mode(rule_set: Literal["set1", "set2", "combined"], persist_env: bool = False) -> None:
    set_active_rule_set(rule_set)
    if persist_env:
        cfg_path = Path(__file__).resolve().parent.parent / "config.py"
        if cfg_path.exists():
            try:
                content = cfg_path.read_text()
                updated = re.sub(
                    r'(fund_rule_set:\s*Literal\["set1",\s*"set2",\s*"combined"\]\s*=\s*)"(?:set1|set2|combined)"',
                    rf'\1"{rule_set}"',
                    content,
                    count=1,
                )
                if updated != content:
                    cfg_path.write_text(updated)
                    get_settings.cache_clear()
            except OSError as exc:
                log.warning("Could not persist fund_rule_set to config.py: %s", exc)


class RuleSetItem(BaseModel):
    id: str
    label: str
    description: str
    highlights: List[str]


class RuleSetStatus(BaseModel):
    active: str
    default: str
    available: List[RuleSetItem]


class SetRuleSetRequest(BaseModel):
    rule_set: Literal["set1", "set2", "combined"]
    persist_env: bool = False


class DataModeStatus(BaseModel):
    data_mode: Literal["synthetic", "real"]
    default_data_mode: Literal["synthetic", "real"]
    real_data_provider: str
    available_providers: List[str]
    db_path: str
    fund_count: int
    latest_nav_date: Optional[date] = None


class SetDataModeRequest(BaseModel):
    data_mode: Literal["synthetic", "real"]
    auto_seed: bool = True
    persist_env: bool = True


class SyncDataRequest(BaseModel):
    data_mode: Optional[Literal["synthetic", "real"]] = None
    provider: Optional[Literal["mfapi_amfi", "amfi_only", "yfinance"]] = None
    refresh_amfi: bool = False
    refresh_master: bool = False
    refresh_ter: bool = False
    refresh_aum: bool = False
    ter_file: Optional[str] = None
    aum_file: Optional[str] = None


class SyncDataResponse(BaseModel):
    status: str
    data_mode: Literal["synthetic", "real"]
    provider: str
    funds_loaded: int
    schemes_synced: int
    nav_points_saved: int
    latest_nav_date: Optional[date] = None
    master_schemes_loaded: int = 0
    ter_matched: int = 0
    aum_matched: int = 0
    errors: List[str] = []


class AdviseRequest(BaseModel):
    query: Optional[str] = ""
    amount: Optional[float] = None
    horizon_years: Optional[float] = None
    risk_preference: Optional[Literal[
        "capital_preservation", "conservative", "moderate", "growth", "aggressive"
    ]] = None
    purpose: Optional[str] = None
    excluded_categories: Optional[List[str]] = None
    emergency_months: Optional[float] = None
    savings_rate: Optional[float] = None
    income_stability: Optional[Literal["stable", "variable"]] = None
    assume_yes: bool = True
    data_mode: Optional[Literal["synthetic", "real"]] = None
    rule_set: Optional[Literal["set1", "set2", "combined"]] = None
    include_stocks: bool = False
    stock_rule_set: Optional[Literal["set1", "set2"]] = None
    trend_based_stocks: bool = True
    min_market_cap_cr: Optional[float] = Field(default=None, ge=0.0, le=5_000_000.0)
    min_traded_value_cr: Optional[float] = Field(default=None, ge=0.0, le=500_000.0)
    portfolio_spec: Optional[PortfolioSpec] = None
    stock360_veto: Optional[bool] = None
    stock360_min_sell_signals: Optional[int] = Field(default=None, ge=1, le=10)


class AdviseResponse(BaseModel):
    run_id: str
    data_mode: Literal["synthetic", "real"] = "synthetic"
    rule_set: Literal["set1", "set2", "combined"] = "set1"
    include_stocks: bool = False
    stock_rule_set: Literal["set1", "set2"] = "set2"
    trend_based_stocks: bool = False
    trend_regime: dict = {}
    stock_filters_applied: dict = {}
    stock_sync_as_of: Optional[str] = None
    stock_warnings: List[str] = []
    portfolio_spec: Optional[dict] = None
    spec_report: dict = {}
    input_mode: Literal["structured", "hybrid", "natural_language"] = "natural_language"
    provenance: Optional[List[dict]] = None
    goal: dict
    profile: dict
    target: dict
    assumptions: dict
    recommendations: List[dict]
    news_context: dict = {}
    unallocated_weight: float
    escalations: List[str]
    disclosures: List[str]
    message: Optional[str] = None


@app.on_event("startup")
def startup_event():
    s = get_settings()
    setup_logging(s.log_level, log_dir=s.log_dir, log_file="backend.log")
    log.info(
        "Wealth Advisor API started (mode=%s, db=%s, logging to %s/backend.log)",
        get_active_mode(),
        s.active_db_path(get_active_mode()),
        s.log_dir,
    )


def get_db_conn(data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    s = get_settings()
    mode = data_mode or get_active_mode()
    conn = open_db(s.active_db_path(mode))
    try:
        yield conn
    finally:
        conn.close()


def get_history_db_conn():
    s = get_settings()
    conn = open_db(s.history_db_path)
    try:
        yield conn
    finally:
        conn.close()


def _build_mode_status(conn, mode: Literal["synthetic", "real"]) -> DataModeStatus:
    s = get_settings()
    store = MetadataStore(conn)
    funds = store.query_funds()
    return DataModeStatus(
        data_mode=mode,
        default_data_mode=s.data_mode,
        real_data_provider=s.real_data_provider,
        available_providers=["mfapi_amfi", "amfi_only", "yfinance"],
        db_path=str(s.active_db_path(mode)),
        fund_count=len(funds),
        latest_nav_date=store.latest_nav_date(),
    )


@app.get("/api/config/data-mode", response_model=DataModeStatus)
def get_data_mode_endpoint(conn=Depends(get_db_conn)):
    mode = get_active_mode()
    return _build_mode_status(conn, mode)


@app.post("/api/config/data-mode", response_model=DataModeStatus)
def set_data_mode_endpoint(req: SetDataModeRequest):
    s = get_settings()
    set_active_mode(req.data_mode, persist_env=req.persist_env)
    conn = open_db(s.active_db_path(req.data_mode))
    try:
        store = MetadataStore(conn)
        if req.auto_seed and store.latest_nav_date() is None:
            if req.data_mode == "synthetic":
                bootstrap_demo(conn)
            else:
                bootstrap_real(conn)
        return _build_mode_status(conn, req.data_mode)
    except Exception as exc:
        log.exception("Failed to initialize data mode %s", req.data_mode)
        raise HTTPException(status_code=500, detail=f"Failed to initialize {req.data_mode} data: {exc}") from exc
    finally:
        conn.close()


@app.get("/api/config/rule-set", response_model=RuleSetStatus)
def get_rule_set_endpoint():
    return RuleSetStatus(
        active=active_rule_set(),
        default=get_default_rule_set(),
        available=[RuleSetItem(**item) for item in AVAILABLE_RULE_SETS],
    )


@app.post("/api/config/rule-set", response_model=RuleSetStatus)
def set_rule_set_endpoint(req: SetRuleSetRequest):
    set_rule_set_mode(req.rule_set, persist_env=req.persist_env)
    return RuleSetStatus(
        active=active_rule_set(),
        default=get_default_rule_set(),
        available=[RuleSetItem(**item) for item in AVAILABLE_RULE_SETS],
    )


@app.get("/api/rules")
def get_rules_endpoint(rule_set: Optional[Literal["set1", "set2", "combined"]] = Query(default=None)):
    target = rule_set or active_rule_set()
    return describe_rule_set(target)


@app.get("/api/rules/compare")
def compare_rules_endpoint():
    return compare_rule_sets()


@app.post("/api/data/sync", response_model=SyncDataResponse)
def sync_data_endpoint(req: SyncDataRequest):
    s = get_settings()
    mode = req.data_mode or get_active_mode()
    conn = open_db(s.active_db_path(mode))
    try:
        store = MetadataStore(conn)
        if mode == "synthetic":
            count = bootstrap_demo(conn)
            return SyncDataResponse(
                status="ok",
                data_mode="synthetic",
                provider="synthetic_demo",
                funds_loaded=count,
                schemes_synced=count,
                nav_points_saved=count * 1600,
                latest_nav_date=store.latest_nav_date(),
                errors=[],
            )
        master_loaded = 0
        ter_matched = 0
        aum_matched = 0
        errors: list[str] = []
        if req.refresh_master:
            from wealth_advisor.data.amfi import fetch_nav_all, sync_scheme_master

            try:
                nav_text = fetch_nav_all()
                master_loaded, rej = sync_scheme_master(store, nav_text)
                errors.extend(rej[:20])
            except Exception as exc:
                errors.append(f"refresh_master failed: {exc}")
        if req.refresh_ter or req.ter_file:
            from wealth_advisor.data.amfi_ter import fetch_ter_text, load_ter_file, load_ter_into_master

            try:
                ter_rep = (
                    load_ter_file(store, req.ter_file)
                    if req.ter_file
                    else load_ter_into_master(store, fetch_ter_text())
                )
                ter_matched = ter_rep.rows_matched
                errors.extend(ter_rep.rejected[:20])
            except Exception as exc:
                errors.append(f"refresh_ter failed: {exc}")
        if req.refresh_aum or req.aum_file:
            from wealth_advisor.data.amfi_aum import fetch_aum_text, load_aum_file, load_aum_into_master

            try:
                aum_rep = (
                    load_aum_file(store, req.aum_file)
                    if req.aum_file
                    else load_aum_into_master(store, fetch_aum_text())
                )
                aum_matched = aum_rep.rows_matched
                errors.extend(aum_rep.rejected[:20])
            except Exception as exc:
                errors.append(f"refresh_aum failed: {exc}")

        rep = bootstrap_real(
            conn,
            provider_name=req.provider,
            refresh_amfi=req.refresh_amfi,
        )
        errors.extend(rep.errors)
        return SyncDataResponse(
            status="ok",
            data_mode="real",
            provider=rep.provider,
            funds_loaded=rep.funds_loaded,
            schemes_synced=rep.schemes_synced,
            nav_points_saved=rep.nav_points_saved,
            latest_nav_date=rep.latest_nav_date,
            master_schemes_loaded=master_loaded,
            ter_matched=ter_matched,
            aum_matched=aum_matched,
            errors=errors,
        )
    except Exception as exc:
        log.exception("Data sync failed for mode=%s", mode)
        raise HTTPException(status_code=500, detail=f"Data sync failed: {exc}") from exc
    finally:
        conn.close()


class AdvisePreviewResponse(BaseModel):
    goal: dict
    portfolio_spec: Optional[dict] = None
    understood: List[str] = []
    warnings: List[str] = []
    custom: bool = False
    llm_calls_budget: int


@app.post("/api/advise/preview", response_model=AdvisePreviewResponse)
def advise_preview_endpoint(req: AdviseRequest):
    """Parse the AI notes only, so the user can confirm what was understood before advice runs."""
    from wealth_advisor.agents.profiling import ProfilingAgent
    from wealth_advisor.domain.portfolio_spec import describe_spec, llm_budget_for
    from wealth_advisor.models.enums import RiskPreference

    s = get_settings()
    try:
        base_llm = getattr(app.state, "llm", None) or get_llm()
        agent = ProfilingAgent(BudgetedLLM(base_llm, s.max_llm_calls_per_run))
        risk_pref = RiskPreference(req.risk_preference) if req.risk_preference else None
        res = agent.resolve(
            req.query, amount=req.amount, horizon_years=req.horizon_years,
            risk_preference=risk_pref, purpose=req.purpose,
            excluded_categories=req.excluded_categories,
        )
    except Exception as exc:
        log.exception("Advice preview failed")
        raise HTTPException(status_code=502, detail="Could not read the notes. Check backend logs and LLM settings.") from exc
    flush_langfuse()
    if res.rejected:
        raise HTTPException(status_code=400, detail=res.rejected.message)
    if res.missing:
        raise HTTPException(status_code=400, detail="Missing info: " + ", ".join(res.missing))
    spec = res.goal.spec
    custom = bool(spec and spec.is_custom)
    return AdvisePreviewResponse(
        goal=res.goal.model_dump(mode="json", exclude={"spec"}),
        portfolio_spec=spec.model_dump(mode="json") if spec else None,
        understood=describe_spec(spec) if custom else [],
        warnings=list(spec.warnings) if spec else [],
        custom=custom,
        llm_calls_budget=llm_budget_for(spec, s),
    )


@app.get("/api/trend-view")
def get_trend_view_endpoint():
    from wealth_advisor.data.trend_view import load_trend_view, trend_view_status

    s = get_settings()
    view = load_trend_view(s.trend_view_path)
    return {"status": trend_view_status(view, s), "view": view.model_dump(mode="json") if view else None}


@app.post("/api/trend-view/refresh")
def refresh_trend_view_endpoint():
    """Rebuild the web trend view from fresh Google News headlines (one LLM call)."""
    from wealth_advisor.data.trend_view import refresh_trend_view, trend_view_status

    s = get_settings()
    try:
        base_llm = getattr(app.state, "llm", None) or get_llm()
        view = refresh_trend_view(BudgetedLLM(base_llm, 2), s)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Trend view refresh failed")
        raise HTTPException(status_code=502, detail="Trend view refresh failed. Check backend logs.") from exc
    finally:
        flush_langfuse()
    return {"status": trend_view_status(view, s), "view": view.model_dump(mode="json")}


@app.post("/api/advise", response_model=AdviseResponse)
def advise_endpoint(
    req: AdviseRequest,
    conn=Depends(get_db_conn),
    history_conn=Depends(get_history_db_conn),
):
    rid = new_run_id()
    active_mode = req.data_mode or get_active_mode()
    if req.data_mode and req.data_mode != get_active_mode():
        set_active_mode(req.data_mode)
        s_override = get_settings()
        conn = open_db(s_override.active_db_path(req.data_mode))

    has_structured = any(
        v is not None
        for v in (req.amount, req.horizon_years, req.risk_preference, req.purpose, req.excluded_categories)
    )
    has_query = bool((req.query or "").strip())
    if has_structured and has_query:
        input_mode: Literal["structured", "hybrid", "natural_language"] = "hybrid"
    elif has_structured:
        input_mode = "structured"
    else:
        input_mode = "natural_language"

    try:
        s = get_settings()
        base_llm = getattr(app.state, "llm", None) or get_llm()
        client = BudgetedLLM(base_llm, s.max_llm_calls_per_run)

        from wealth_advisor.agents.profiling import ProfileAssumptions, ProfilingAgent
        from wealth_advisor.models.enums import IncomeStability, RiskPreference

        agent = ProfilingAgent(client)
        risk_pref = RiskPreference(req.risk_preference) if req.risk_preference else None
        with trace_context(run_id=rid):
            res = agent.resolve(
                req.query,
                amount=req.amount,
                horizon_years=req.horizon_years,
                risk_preference=risk_pref,
                purpose=req.purpose,
                excluded_categories=req.excluded_categories,
                spec=req.portfolio_spec,
            )
    except Exception as exc:
        flush_langfuse()
        log.exception("Advice profiling request failed")
        raise HTTPException(
            status_code=502,
            detail="The advice service could not complete the request. Check backend logs and LLM provider settings.",
        ) from exc

    if res.rejected:
        flush_langfuse()
        raise HTTPException(status_code=400, detail=res.rejected.message)
    if res.missing:
        flush_langfuse()
        raise HTTPException(status_code=400, detail="Missing info: " + ", ".join(res.missing))

    goal = res.goal
    assumptions = None
    if any(v is not None for v in (req.emergency_months, req.savings_rate, req.income_stability)):
        assumptions = ProfileAssumptions(
            savings_rate=req.savings_rate if req.savings_rate is not None else s.default_savings_rate,
            emergency_months=req.emergency_months if req.emergency_months is not None else s.default_emergency_months,
            income_stability=IncomeStability(req.income_stability) if req.income_stability else IncomeStability.STABLE,
        )

    try:
        store = MetadataStore(conn)
        if store.latest_nav_date() is None:
            if active_mode == "synthetic":
                bootstrap_demo(conn)
            else:
                bootstrap_real(conn)
        result = advise(
            goal,
            client,
            conn,
            assumptions=assumptions,
            run_id=rid,
            rule_set=req.rule_set,
            include_stocks=req.include_stocks,
            stock_rule_set=req.stock_rule_set,
            trend_based_stocks=req.trend_based_stocks,
            min_market_cap_cr=req.min_market_cap_cr,
            min_traded_value_cr=req.min_traded_value_cr,
            stock360_veto=req.stock360_veto,
            stock360_min_sell_signals=req.stock360_min_sell_signals,
        )

        d = result.decision
        verdict_map = {v.instrument_id: v for v in result.verdicts}
        rec_map = {rec.instrument_id: rec for rec in d.record.recommendations}
        
        recs = []
        from wealth_advisor.orchestration.stock360_veto import report_badges
        stock_syms = [r.instrument_id for r in d.rows if r.instrument_type == "stock" or r.category == "Direct Equity"]
        badges = report_badges(conn, stock_syms, active_mode) if stock_syms else {}
        for r in d.rows:
            v = verdict_map.get(r.instrument_id)
            rec_obj = rec_map.get(r.instrument_id)
            fund_rec = store.find_fund(r.instrument_id)
            ns = result.news_summaries.get(r.instrument_id)
            is_stock = r.instrument_type == "stock" or r.category == "Direct Equity"
            recs.append({
                "instrument_id": r.instrument_id,
                "instrument_type": "stock" if is_stock else "mf",
                "symbol": r.symbol if is_stock else None,
                "sector": r.sector if is_stock else None,
                "market_cap_cr": r.market_cap_cr if is_stock else None,
                "checks_passed": r.checks_passed if is_stock else None,
                "name": r.name,
                "action": rec_obj.action.value if rec_obj else "BUY",
                "bucket": r.bucket.value,
                "category": r.category,
                "plan": fund_rec.plan if fund_rec else ("direct_equity" if is_stock else "direct"),
                "amc": fund_rec.amc if fund_rec else (r.sector or "NSE" if is_stock else ""),
                "aum_cr": fund_rec.aum_cr if fund_rec else (r.market_cap_cr if is_stock else None),
                "source": fund_rec.source if fund_rec else ("nse_stock" if is_stock else ""),
                "weight": r.weight,
                "amount": r.amount,
                "raw_amount": round(result.goal.amount * r.weight / 100, 2),
                "expense_ratio": r.expense_ratio,
                "rating": r.rating,
                "cagr_5y": r.cagr_5y,
                "score": r.score,
                "confidence": v.confidence if v else None,
                "sub_scores": v.sub_scores if v else {},
                "pros": v.pros if v else [],
                "cons": v.cons if v else [],
                "change_conditions": v.change_conditions if v else [],
                "rationale": r.rationale,
                "citations": r.citation_ids,
                "citation_details": [c.model_dump(mode="json") for c in rec_obj.citations] if rec_obj else [],
                "news_summary": ns.model_dump(mode="json") if ns else None,
                "report360": badges.get(r.instrument_id) if is_stock else None,
            })

        as_of_dt = store.latest_nav_date() or date.today()
        live_flag = active_mode == "real" and getattr(client, "model", "") != "scripted"
        macro_items = [
            item.model_dump(mode="json")
            for item in get_macro_and_regulatory_news(as_of=as_of_dt, fetch_live=live_flag)
        ]
        fund_news_list = [
            result.news_summaries[r.instrument_id].model_dump(mode="json")
            for r in d.rows
            if r.instrument_id in result.news_summaries
        ]

        resp = AdviseResponse(
            run_id=rid,
            data_mode=active_mode,
            rule_set=result.rule_set,
            include_stocks=result.include_stocks,
            stock_rule_set=result.stock_rule_set,  # type: ignore[arg-type]
            trend_based_stocks=result.trend_based_stocks,
            trend_regime=result.trend_regime,
            stock_filters_applied=result.stock_filters_applied,
            stock_sync_as_of=result.stock_sync_as_of,
            stock_warnings=result.stock_warnings,
            portfolio_spec=result.portfolio_spec.model_dump(mode="json") if result.portfolio_spec else None,
            spec_report=result.spec_report,
            input_mode=input_mode,
            provenance=result.provenance,
            goal=result.goal.model_dump(mode="json"),
            profile={
                "profile_id": result.profile.profile_id,
                "version": result.profile.version,
                "risk_score": result.profile.risk_score,
                "category": result.profile.category.value,
                "allocation_band": {k.value: list(v) for k, v in result.profile.allocation_band.ranges.items()},
                "risk_factors": result.profile.inputs.get("risk_factors", {}),
            },
            target={b.value: w for b, w in result.target.items()},
            assumptions=result.assumptions.model_dump(mode="json"),
            recommendations=recs,
            news_context={
                "as_of": as_of_dt.isoformat(),
                "live_rss_enabled": live_flag,
                "macro_news": macro_items,
                "fund_news": fund_news_list,
            },
            unallocated_weight=d.unallocated_weight,
            escalations=d.escalations,
            disclosures=[
                *result.compliance.disclosures,
                *([REPORT_DISCLOSURE] if any(b for b in badges.values()) else []),
            ],
            message="Success"
        )

        # Save advice run to history (never break /api/advise if saving fails)
        owned_h_conn = False
        h_conn = history_conn
        try:
            if not isinstance(h_conn, sqlite3.Connection):
                h_conn = open_db(get_settings().history_db_path)
                owned_h_conn = True
            h_store = HistoryStore(h_conn)
            title = build_run_title(
                result.goal.purpose or "Wealth Creation",
                result.goal.amount,
                result.goal.horizon_years,
                query=req.query,
            )
            h_store.save(
                run_id=rid,
                title=title,
                request_dict=req.model_dump(mode="json"),
                response_dict=resp.model_dump(mode="json"),
                context_dict=result.build_context(),
                data_mode=active_mode,
            )
        except Exception as exc:
            log.warning("Failed to save advice run %s to history: %s", rid, exc)
        finally:
            if owned_h_conn and isinstance(h_conn, sqlite3.Connection):
                h_conn.close()

        return resp
    except SuitabilityError as exc:
        raise HTTPException(status_code=403, detail=f"Blocked by compliance: {exc}")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

class Fund(BaseModel):
    code: str
    name: str
    category: str
    expense_ratio: Optional[float]
    aum_cr: Optional[float]
    rating: Optional[float]
    source: str
    as_of: date

@app.get("/api/funds", response_model=List[Fund])
def get_funds(category: Optional[str] = None, max_expense: Optional[float] = None, min_aum: Optional[float] = None, min_rating: Optional[float] = None, plan: Optional[str] = None, conn = Depends(get_db_conn)):

    store = MetadataStore(conn)
    rows = store.query_funds(category=category, max_expense=max_expense, min_aum=min_aum, min_rating=min_rating, plan=plan)
    return [
        Fund(
            code=f.code,
            name=f.name,
            category=f.category,
            expense_ratio=f.expense_ratio,
            aum_cr=f.aum_cr,
            rating=f.rating,
            source=f.source,
            as_of=f.as_of
        )
        for f in rows
    ]


def _peer_median_ytm(store: MetadataStore) -> float | None:
    from statistics import median
    from wealth_advisor.models.enums import Bucket

    debt_ytms = [
        f.ytm
        for f in store.query_funds(bucket=Bucket.DEBT)
        if f.ytm is not None
    ]
    return float(median(debt_ytms)) if debt_ytms else None


class FundRefreshRequest(BaseModel):
    include_ter_aum: bool = False
    include_news: bool = True


@app.post("/api/funds/{code}/refresh")
def refresh_fund_endpoint(
    code: str,
    req: Optional[FundRefreshRequest] = None,
    data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None),
    conn=Depends(get_db_conn),
):
    from wealth_advisor.data.fund_refresh import refresh_fund

    eff_mode = data_mode or get_active_mode()
    if eff_mode == "synthetic" or code.upper().startswith("DEMO"):
        raise HTTPException(
            status_code=409,
            detail="Live refresh is only supported in Real data mode for AMFI schemes.",
        )
    if not code.isdigit():
        raise HTTPException(
            status_code=422,
            detail=f"Scheme code '{code}' must be a numeric AMFI code.",
        )
    store = MetadataStore(conn)
    if store.get_fund(code) is None and store.get_master(code) is None:
        raise HTTPException(status_code=404, detail=f"Fund '{code}' not found")

    body = req or FundRefreshRequest()
    report = refresh_fund(
        store,
        code,
        include_ter_aum=body.include_ter_aum,
        include_news=body.include_news,
        data_mode=eff_mode,
    )
    return report.model_dump(mode="json")


@app.get("/api/funds/{code}/refresh-log")
def get_fund_refresh_log(
    code: str,
    limit: int = Query(default=10, ge=1, le=100),
    conn=Depends(get_db_conn),
):
    store = MetadataStore(conn)
    if store.get_fund(code) is None and store.get_master(code) is None:
        raise HTTPException(status_code=404, detail=f"Fund '{code}' not found")
    return [r.model_dump(mode="json") for r in store.refresh_history(code, limit=limit)]


@app.get("/api/funds/{code}")
def get_fund_detail(
    code: str,
    run_id: Optional[str] = None,
    data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None),
    conn=Depends(get_db_conn),
):
    from wealth_advisor.agents.tools import facts_for_fund
    from wealth_advisor.audit.log import AuditLog
    from wealth_advisor.data.on_demand import compute_fund_profile
    from wealth_advisor.domain.fund_compare import build_checklist
    from wealth_advisor.domain.scoring import score_instrument
    from wealth_advisor.domain.subscores import (
        confidence as calc_confidence,
        detect_conflicts,
        fit_score,
        fundamental_score,
        liquidity_cost_score,
    )
    from wealth_advisor.models.enums import Bucket
    from wealth_advisor.rag.index import VectorIndex

    eff_mode = data_mode or get_active_mode()
    store = MetadataStore(conn)
    fund = store.find_fund(code)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{code}' not found")

    nav_s = store.nav_series(code)
    if len(nav_s) == 0 and eff_mode == "real" and not code.startswith("DEMO_"):
        try:
            from wealth_advisor.data.on_demand import ensure_nav
            from wealth_advisor.data.stats import compute_all_stats

            ensure_nav(store, code, fetch_benchmark=True)
            nav_s = store.nav_series(code)
            if len(nav_s) >= 30 and store.get_stats(code) is None:
                compute_all_stats(store)
                fund = store.find_fund(code) or fund
        except Exception as exc:  # noqa: BLE001
            log.debug("On-demand NAV fetch in get_fund_detail skipped for %s: %s", code, exc)

    profile = compute_fund_profile(
        store,
        code,
        fetch_if_missing=(eff_mode == "real" and not code.startswith("DEMO_")),
    )
    peer_med_ytm = _peer_median_ytm(store) if profile.bucket == Bucket.DEBT else None
    checklist_items = build_checklist(profile, peer_median_ytm=peer_med_ytm)
    refresh_rows = store.refresh_history(code, limit=1)
    last_refresh = refresh_rows[0].model_dump(mode="json") if refresh_rows else None

    stats = store.get_stats(code)
    as_of = store.latest_nav_date() or fund.as_of

    # NAV series (sample to <= 160 points including first and last for responsive charting)
    nav_history = []
    if len(nav_s) > 0:
        step = max(1, len(nav_s) // 150)
        sampled = nav_s.iloc[::step]
        if sampled.index[-1] != nav_s.index[-1]:
            sampled = nav_s.iloc[list(range(0, len(nav_s), step)) + [len(nav_s) - 1]]
        nav_history = [
            {"date": idx.strftime("%Y-%m-%d"), "nav": round(float(val), 4)}
            for idx, val in sampled.items()
        ]

    # RAG documents for this fund
    index = VectorIndex(conn)
    docs = index.search(fund.category, isin=fund.isin, k=5, as_of=as_of) if fund.isin else []
    facts = facts_for_fund(fund, stats, as_of, docs)
    fm = {f.name: f.value for f in facts}

    # Check if we have an audited run with exact verdict & sizing for this fund
    run_context = None
    verdict_data = None
    if run_id:
        audit_entries = AuditLog(conn).entries(run_id)
        if audit_entries:
            prof_entry = next((e for e in audit_entries if e.step == "profile"), None)
            res_entry = next((e for e in audit_entries if e.step == "research"), None)
            ana_entry = next((e for e in audit_entries if e.step == "analysis"), None)
            dec_entry = next((e for e in audit_entries if e.step == "decision"), None)
            comp_entry = next((e for e in audit_entries if e.step == "compliance"), None)

            prof_step = prof_entry.payload if prof_entry else None
            res_step = res_entry.payload if res_entry else None
            ana_step = ana_entry.payload if ana_entry else None
            dec_step = dec_entry.payload if dec_entry else None
            comp_step = comp_entry.payload if comp_entry else None

            # Use exact facts from the run's research dossier if recorded
            if res_step:
                mf_entries = (res_step.get("dossier") or {}).get("mf")
                if isinstance(mf_entries, list):
                    matched_entry = next(
                        (
                            item
                            for item in mf_entries
                            if (item.get("instrument") or {}).get("id") == code
                        ),
                        None,
                    )
                    if matched_entry and matched_entry.get("facts"):
                        from wealth_advisor.models.core import Fact

                        facts = [Fact.model_validate(f) for f in matched_entry["facts"]]
                        fm = {f.name: f.value for f in facts}

            if ana_step:
                verdict_data = next(
                    (v for v in ana_step.get("verdicts", []) if v.get("instrument_id") == code),
                    None,
                )

            research_note = None
            research_tool_calls = 0
            if res_step:
                for run_item in res_step.get("runs", []):
                    notes = run_item.get("notes")
                    if isinstance(notes, dict) and code in notes:
                        research_note = notes[code]
                        research_tool_calls = len(run_item.get("tool_calls") or [])
                        break

            rec_item = None
            pos_item = None
            bucket_sizing = None
            if dec_step:
                recs = dec_step.get("record", {}).get("recommendations", [])
                rec_item = next((r for r in recs if r.get("instrument_id") == code), None)
                for b_name, b_sizing in dec_step.get("sizing", {}).items():
                    for p in b_sizing.get("positions", []):
                        if p.get("id") == code:
                            pos_item = {**p, "bucket": b_name}
                            bucket_sizing = {
                                "bucket": b_name,
                                "bucket_weight": b_sizing.get("weight"),
                                "candidates": b_sizing.get("candidates", []),
                                "positions": b_sizing.get("positions", []),
                            }
            if prof_step or rec_item or pos_item:
                total_amt = (prof_step or {}).get("goal", {}).get("amount", 0)
                w = pos_item["weight"] if pos_item else (rec_item["target_weight"] if rec_item else 0)
                fallbacks = (ana_step or {}).get("deterministic_fallbacks", [])
                run_context = {
                    "run_id": run_id,
                    "goal": (prof_step or {}).get("goal"),
                    "assumptions": (prof_step or {}).get("assumptions"),
                    "risk": (prof_step or {}).get("risk"),
                    "action": rec_item.get("action", "BUY") if rec_item else "BUY",
                    "weight": w,
                    "amount": pos_item.get("amount") if pos_item else None,
                    "raw_amount": round(total_amt * w / 100, 2) if total_amt and w else None,
                    "rationale": rec_item.get("rationale") if rec_item else None,
                    "citations": rec_item.get("citations", []) if rec_item else [],
                    "research_note": research_note,
                    "research_tool_calls": research_tool_calls,
                    "bucket_sizing": bucket_sizing,
                    "sizing_settings": (dec_step or {}).get("settings"),
                    "provenance": {
                        "model": (ana_entry.model if ana_entry and ana_entry.model else None)
                        or (res_entry.model if res_entry and res_entry.model else None),
                        "research_prompt": res_entry.prompt_version if res_entry else None,
                        "analyst_prompt": ana_entry.prompt_version if ana_entry else None,
                        "decision_prompt": dec_entry.prompt_version if dec_entry else None,
                        "is_deterministic_fallback": code in fallbacks,
                        "llm_calls_used": (ana_step or {}).get("llm_calls_used"),
                        "llm_calls_budget": (ana_step or {}).get("llm_calls_budget"),
                    },
                    "disclosures": (comp_step or {}).get("disclosures", []),
                }

    # Fetch real-time / curated news for this fund
    live_fund_news = eff_mode == "real" and not fund.code.startswith("DEMO_")
    fund_news = get_fund_news(
        fund.code,
        fund.name,
        fund.category,
        fund.amc,
        as_of=as_of,
        fetch_live=live_fund_news,
    )

    # Fallback deterministic analysis if no run verdict found
    s = get_settings()
    conflicts = detect_conflicts(fm, fund.category, s)
    if verdict_data is None:
        horizon = (run_context.get("goal") or {}).get("horizon_years", 5.0) if run_context else 5.0
        risk_cat = (run_context.get("risk") or {}).get("category", "Moderate") if run_context else "Moderate"
        sub = {
            "fundamental": fundamental_score(fm),
            "fit": fit_score(fm, fund.category, risk_cat, horizon, s),
            "news": float(fm.get("news_score", fund_news.news_score)),
            "sentiment": float(fm.get("sentiment_score", fund_news.sentiment_score)),
            "liquidity_cost": liquidity_cost_score(fm, fund.category, s),
        }
        verdict_data = {
            "instrument_id": code,
            "score": score_instrument(sub),
            "sub_scores": sub,
            "confidence": calc_confidence(fm, len(conflicts), 0),
            "pros": [],
            "cons": [f"Conflict: {desc}" for desc, _ in conflicts],
            "change_conditions": [],
        }

    track_years = round((as_of - fund.launch_date).days / 365.25, 2) if fund.launch_date else None

    return {
        "fund": {
            **fund.model_dump(mode="json"),
            "track_years": track_years,
            "expense_ceiling": s.expense_ceilings.get(fund.category, s.default_expense_ceiling),
        },
        "stats": stats.model_dump(mode="json") if stats else None,
        "nav_history": nav_history,
        "documents": [
            {
                "section": d.chunk.section,
                "doc_type": d.chunk.doc_type,
                "doc_id": d.chunk.doc_id,
                "as_of": d.chunk.as_of,
                "source_url": d.chunk.source_url,
                "text": d.chunk.text,
                "score": d.score,
                "citation": d.citation.model_dump(mode="json"),
            }
            for d in docs
        ],
        "facts": [f.model_dump(mode="json") for f in facts],
        "analysis": {
            **verdict_data,
            "weights": s.analyst_weights.model_dump(),
            "conflicts": [{"description": desc, "fields": names} for desc, names in conflicts],
        },
        "news": fund_news.model_dump(mode="json"),
        "run_context": run_context,
        "profile": profile.model_dump(mode="json"),
        "checklist": [item.model_dump(mode="json") for item in checklist_items],
        "last_refresh": last_refresh,
    }

class DailyRunRequest(BaseModel):
    profile_id: Optional[str] = None
    goal_id: Optional[str] = None
    as_of: Optional[date] = None
    force: bool = False
    auto_confirm: bool = False


class DailySimulateRequest(BaseModel):
    profile_id: Optional[str] = None
    goal_id: Optional[str] = None
    start_date: Optional[date] = None
    days: int = 30
    auto_confirm: bool = True


class DailyConfirmRequest(BaseModel):
    profile_id: Optional[str] = None
    goal_id: Optional[str] = None


@app.post("/api/portfolio/upload")
async def upload_portfolio(
    file: UploadFile = File(...),
    profile_id: Optional[str] = Query(default=None),
    goal_id: Optional[str] = Query(default=None),
    as_of: Optional[date] = Query(default=None),
    conn=Depends(get_db_conn),
):
    from wealth_advisor.daily import PositionStore
    from wealth_advisor.domain.allocation import target_allocation
    from wealth_advisor.models.core import Goal
    from wealth_advisor.models.enums import Bucket, RiskPreference
    from wealth_advisor.portfolio import (
        PortfolioStore,
        ProfileStore,
        analyze_portfolio_drift,
        parse_portfolio_csv,
        update_profile_from_portfolio,
        valuate_portfolio,
    )

    content_bytes = await file.read()
    try:
        csv_text = content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = content_bytes.decode("latin-1", errors="replace")

    mstore = MetadataStore(conn)
    if mstore.latest_nav_date() is None and get_active_mode() == "synthetic":
        bootstrap_demo(conn)

    eval_date = as_of or mstore.latest_nav_date() or date.today()
    parse_res = parse_portfolio_csv(csv_text, mstore, max_as_of=eval_date)
    valuation = valuate_portfolio(parse_res.holdings, mstore, as_of=eval_date)

    pos_store = PositionStore(conn)
    all_positions = pos_store.list_all()
    if profile_id:
        pos = pos_store.get(profile_id, goal_id)
    else:
        pos = all_positions[0] if all_positions else None

    prof_store = ProfileStore(conn)
    effective_profile_id = profile_id or (pos.profile_id if pos else "demo-profile")
    existing_profile = prof_store.latest(effective_profile_id)
    if existing_profile is None and pos is not None:
        existing_profile = pos.profile

    updated_profile = None
    if valuation.holdings:
        goal_obj = (
            pos.goal
            if pos is not None
            else Goal(
                amount=max(valuation.total_value, 10_000.0),
                horizon_years=5.0,
                risk_preference=RiskPreference.MODERATE,
            )
        )
        updated_profile, updated_assumptions = update_profile_from_portfolio(
            conn,
            valuation,
            goal_obj,
            prev_profile=existing_profile,
            assumptions=pos.assumptions if pos is not None else None,
        )
        effective_profile_id = updated_profile.profile_id
        PortfolioStore(conn).save(effective_profile_id, parse_res.holdings, valuation)
        if pos is not None:
            pos.profile = updated_profile
            pos.assumptions = updated_assumptions
            pos.holdings = list(parse_res.holdings)
            pos_store.save(pos)

    target_weights: dict[Bucket, float] = {}
    if pos is not None:
        target_weights = dict(pos.target)
    elif updated_profile is not None:
        target_weights = target_allocation(updated_profile.allocation_band)
    elif valuation.bucket_weights:
        target_weights = dict(valuation.bucket_weights)

    drift_report = analyze_portfolio_drift(valuation, target_weights) if target_weights else None

    return {
        "filename": file.filename,
        "profile_id": effective_profile_id,
        "as_of": eval_date.isoformat(),
        "holdings_count": len(valuation.holdings),
        "total_cost": valuation.total_cost,
        "total_value": valuation.total_value,
        "total_unrealized_gain": valuation.total_unrealized_gain,
        "hhi": valuation.hhi,
        "bucket_values": {k.value: v for k, v in valuation.bucket_values.items()},
        "bucket_weights": {k.value: v for k, v in valuation.bucket_weights.items()},
        "holdings": [h.model_dump(mode="json") for h in valuation.holdings],
        "drift": drift_report.model_dump(mode="json") if drift_report else None,
        "profile_version": updated_profile.version if updated_profile else (existing_profile.version if existing_profile else None),
        "errors": [f"Row {e.row} ({e.field}): {e.message}" for e in parse_res.errors],
        "row_errors": [e.model_dump(mode="json") for e in parse_res.errors],
    }


@app.post("/api/daily/run")
def daily_run_endpoint(req: DailyRunRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.daily import PositionStore, run_daily_cycle
    from wealth_advisor.notifications import ConsoleNotifier

    mstore = MetadataStore(conn)
    if mstore.latest_nav_date() is None and get_active_mode() == "synthetic":
        bootstrap_demo(conn)

    pos_store = PositionStore(conn)
    if req.profile_id:
        pos = pos_store.get(req.profile_id, req.goal_id)
    else:
        all_pos = pos_store.list_all()
        pos = all_pos[0] if all_pos else None
    if pos is None:
        raise HTTPException(status_code=404, detail="No active position found")

    eval_date = req.as_of or mstore.latest_nav_date() or date.today()
    llm_client = getattr(app.state, "llm", None) or get_llm()
    notifier = ConsoleNotifier(quiet=True)
    res = run_daily_cycle(
        conn,
        llm_client,
        profile_id=pos.profile_id,
        goal_id=pos.goal_id,
        as_of=eval_date,
        notifier=notifier,
        force_refresh=req.force,
        auto_confirm=req.auto_confirm,
    )
    return res.model_dump(mode="json")


@app.post("/api/daily/simulate")
def daily_simulate_endpoint(req: DailySimulateRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.daily import PositionStore, simulate_daily_loop
    from wealth_advisor.notifications import ConsoleNotifier

    mstore = MetadataStore(conn)
    if mstore.latest_nav_date() is None and get_active_mode() == "synthetic":
        bootstrap_demo(conn)

    pos_store = PositionStore(conn)
    if req.profile_id:
        pos = pos_store.get(req.profile_id, req.goal_id)
    else:
        all_pos = pos_store.list_all()
        pos = all_pos[0] if all_pos else None
    if pos is None:
        raise HTTPException(status_code=404, detail="No active position found")

    llm_client = getattr(app.state, "llm", None) or get_llm()
    notifier = ConsoleNotifier(quiet=True)
    report = simulate_daily_loop(
        conn,
        llm_client,
        profile_id=pos.profile_id,
        goal_id=pos.goal_id,
        days=req.days,
        start_date=req.start_date,
        notifier=notifier,
        auto_confirm=req.auto_confirm,
    )
    return report.model_dump(mode="json")


@app.post("/api/daily/confirm")
def daily_confirm_endpoint(req: DailyConfirmRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.daily import PositionStore, confirm_rebalance

    pos_store = PositionStore(conn)
    if req.profile_id:
        pos = pos_store.get(req.profile_id, req.goal_id)
    else:
        all_pos = pos_store.list_all()
        pos = all_pos[0] if all_pos else None
    if pos is None:
        raise HTTPException(status_code=404, detail="No active position found")

    try:
        updated = confirm_rebalance(conn, pos.profile_id, pos.goal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return updated.model_dump(mode="json")



class CompareRequest(BaseModel):
    codes: List[str]
    narrative: bool = True
    data_mode: Optional[Literal["synthetic", "real"]] = None


class CompareResponse(BaseModel):
    run_id: str
    as_of: date
    funds: List[dict]
    fund_a: dict
    fund_b: dict
    criteria: List[dict]
    checklist: dict
    scores: dict[str, float]
    winner: Literal["A", "B", "tie"]
    winner_code: Optional[str] = None
    winner_name: Optional[str] = None
    ranking: List[str]
    same_category: bool
    warnings: List[str] = []
    not_evaluated: List[dict]
    narrative: str = ""
    narrative_mode: Literal["llm", "template", "disabled"] = "disabled"
    disclosures: List[str]
    citations: List[dict] = []
    news_comparison: Optional[dict] = None


@app.get("/api/news")
def get_news_endpoint(
    code: Optional[str] = None,
    live: Optional[bool] = None,
    conn=Depends(get_db_conn),
):
    store = MetadataStore(conn)
    as_of = store.latest_nav_date() or date.today()
    fetch_live = live if live is not None else (get_active_mode() == "real")
    macro = [
        item.model_dump(mode="json")
        for item in get_macro_and_regulatory_news(as_of=as_of, fetch_live=fetch_live)
    ]
    if not code:
        return {"as_of": as_of.isoformat(), "macro_news": macro, "fund_news": None}
    fund = store.get_fund(code)
    master = store.get_master(code) if fund is None else None
    if fund is None and master is None:
        raise HTTPException(status_code=404, detail=f"Fund '{code}' not found")
    name = fund.name if fund else master.name
    category = fund.category if fund else master.category
    amc = fund.amc if fund else master.amc
    summary = get_fund_news(
        code,
        name,
        category,
        amc,
        as_of=as_of,
        fetch_live=fetch_live and not code.startswith("DEMO_"),
    )
    return {
        "as_of": as_of.isoformat(),
        "macro_news": macro,
        "fund_news": summary.model_dump(mode="json"),
    }


@app.get("/api/compare/schemes")
def search_compare_schemes(
    q: str = "",
    category: Optional[str] = None,
    plan: Optional[str] = "direct",
    option: Optional[str] = "growth",
    limit: int = Query(default=50, ge=1, le=200),
    conn=Depends(get_db_conn),
):
    cat_filter = None if (not category or category.lower() == "all") else category
    plan_filter = None if (not plan or plan.lower() == "all") else plan.lower()
    opt_filter = None if (not option or option.lower() == "all") else option.lower()

    store = MetadataStore(conn)
    master_rows = store.search_master(
        q=q,
        category=cat_filter,
        plan=plan_filter,
        option=opt_filter,
        limit=limit,
    )
    items: list[dict] = [r.model_dump(mode="json") for r in master_rows]
    seen_codes = {r.code for r in master_rows}

    if len(items) < limit:
        funds = store.query_funds(
            category=cat_filter,
            plan=plan_filter,
        )
        if not funds and not items and get_active_mode() == "synthetic":
            bootstrap_demo(conn)
            funds = store.query_funds(
                category=cat_filter,
                plan=plan_filter,
            )
        tokens = [t.lower() for t in q.strip().split() if t]
        for f in funds:
            if f.code in seen_codes:
                continue
            if opt_filter and (f.option or "growth").lower() != opt_filter:
                continue
            hay = f"{f.name} {f.amc} {f.code} {f.isin}".lower()
            if tokens and not all(tok in hay for tok in tokens):
                continue
            items.append(
                {
                    "code": f.code,
                    "isin_growth": f.isin,
                    "isin_reinvest": "",
                    "name": f.name,
                    "amc": f.amc,
                    "sebi_category": f.category,
                    "category": f.category,
                    "plan": f.plan,
                    "option": f.option,
                    "structure": "open",
                    "latest_nav": None,
                    "nav_date": f.as_of.isoformat(),
                    "expense_ratio": f.expense_ratio,
                    "ter_as_of": f.as_of.isoformat(),
                    "aum_cr": f.aum_cr,
                    "aum_as_of": f.as_of.isoformat(),
                    "source": f.source,
                    "as_of": f.as_of.isoformat(),
                }
            )
            seen_codes.add(f.code)
            if len(items) >= limit:
                break
    return items


@app.get("/api/compare/categories")
def get_compare_categories(conn=Depends(get_db_conn)):
    store = MetadataStore(conn)
    cats = store.master_categories()
    if cats:
        return cats
    funds = store.query_funds()
    if not funds and get_active_mode() == "synthetic":
        bootstrap_demo(conn)
        funds = store.query_funds()
    counts: dict[str, int] = {}
    for f in funds:
        counts[f.category] = counts.get(f.category, 0) + 1
    return [{"category": k, "count": counts[k]} for k in sorted(counts)]


@app.post("/api/compare", response_model=CompareResponse)
def compare_funds_endpoint(req: CompareRequest, conn=Depends(get_db_conn)):
    from statistics import median
    from wealth_advisor.agents.compare_narrator import CompareNarrator, comparison_facts
    from wealth_advisor.audit.log import AuditLog, sha256_json
    from wealth_advisor.data.on_demand import compute_fund_profile
    from wealth_advisor.domain.fund_compare import compare_funds
    from wealth_advisor.guardrails.disclosures import DEMO_DISCLOSURE, DISCLOSURES
    from wealth_advisor.models.enums import Bucket

    if len(req.codes) != 2:
        raise HTTPException(status_code=422, detail="Exactly 2 scheme codes are required.")
    code_a, code_b = req.codes[0].strip(), req.codes[1].strip()
    if not code_a or not code_b or code_a == code_b:
        raise HTTPException(status_code=422, detail="Two distinct scheme codes are required.")

    active_mode = req.data_mode or get_active_mode()
    closed_override = False
    if req.data_mode and req.data_mode != get_active_mode():
        s_override = get_settings()
        conn = open_db(s_override.active_db_path(req.data_mode))
        closed_override = True

    try:
        store = MetadataStore(conn)
        if (
            store.get_fund(code_a) is None
            and store.get_master(code_a) is None
            and active_mode == "synthetic"
            and store.latest_nav_date() is None
        ):
            bootstrap_demo(conn)

        for c in (code_a, code_b):
            if store.get_fund(c) is None and store.get_master(c) is None:
                raise HTTPException(status_code=422, detail=f"Unknown scheme code: {c}")

        prof_a = compute_fund_profile(store, code_a, fetch_if_missing=True)
        prof_b = compute_fund_profile(store, code_b, fetch_if_missing=True)

        peer_median_ytm: float | None = None
        if prof_a.bucket == Bucket.DEBT or prof_b.bucket == Bucket.DEBT:
            peer_median_ytm = _peer_median_ytm(store)

        comp = compare_funds(prof_a, prof_b, peer_median_ytm=peer_median_ytm)
        rid = new_run_id()

        llm_client = None
        if req.narrative:
            try:
                llm_client = get_llm()
            except Exception:
                llm_client = None

        narrator = CompareNarrator(llm_client)
        with trace_context(run_id=rid, step="fund_compare"):
            narrative_text, narrative_mode = narrator.narrate(comp, narrative=req.narrative)

        is_demo = code_a.startswith("DEMO_") or code_b.startswith("DEMO_") or active_mode == "synthetic"
        news_a = get_fund_news(
            prof_a.code, prof_a.name, prof_a.category, prof_a.amc,
            as_of=comp.as_of, fetch_live=not is_demo,
        )
        news_b = get_fund_news(
            prof_b.code, prof_b.name, prof_b.category, prof_b.amc,
            as_of=comp.as_of, fetch_live=not is_demo,
        )
        macro_news = get_macro_and_regulatory_news(as_of=comp.as_of, fetch_live=not is_demo)

        combined_news_a = round(0.6 * news_a.news_score + 0.4 * news_a.sentiment_score, 1)
        combined_news_b = round(0.6 * news_b.news_score + 0.4 * news_b.sentiment_score, 1)
        if combined_news_a - combined_news_b >= 1.0:
            news_winner: Literal["A", "B", "tie"] = "A"
            news_summary_text = (
                f"{prof_a.name} holds a stronger news & sentiment profile "
                f"(composite {combined_news_a}/100 vs {combined_news_b}/100 for {prof_b.name}), "
                f"supported by {news_a.positive_count} positive and {news_a.negative_count} negative signal(s)."
            )
        elif combined_news_b - combined_news_a >= 1.0:
            news_winner = "B"
            news_summary_text = (
                f"{prof_b.name} holds a stronger news & sentiment profile "
                f"(composite {combined_news_b}/100 vs {combined_news_a}/100 for {prof_a.name}), "
                f"supported by {news_b.positive_count} positive and {news_b.negative_count} negative signal(s)."
            )
        else:
            news_winner = "tie"
            news_summary_text = (
                f"Both {prof_a.name} ({combined_news_a}/100) and {prof_b.name} ({combined_news_b}/100) "
                f"exhibit comparable news and market sentiment profiles under current category and regulatory conditions."
            )

        news_comparison_payload = {
            "as_of": comp.as_of.isoformat(),
            "live_rss_enabled": not is_demo,
            "fund_a_news": news_a.model_dump(mode="json"),
            "fund_b_news": news_b.model_dump(mode="json"),
            "macro_news": [m.model_dump(mode="json") for m in macro_news],
            "combined_score_a": combined_news_a,
            "combined_score_b": combined_news_b,
            "news_winner": news_winner,
            "summary": news_summary_text,
        }

        all_facts = comparison_facts(comp)
        fact_hashes = {f.id: sha256_json(f.model_dump(mode="json")) for f in all_facts}

        AuditLog(conn).append(
            rid,
            agent="fund_compare",
            step="compare",
            model=getattr(llm_client, "model", "") if (req.narrative and llm_client) else "deterministic",
            payload={
                "inputs": {
                    "codes": [code_a, code_b],
                    "narrative": req.narrative,
                    "data_mode": active_mode,
                },
                "fact_ids": [f.id for f in all_facts],
                "fact_hashes": fact_hashes,
                "result": comp.model_dump(mode="json"),
                "news_comparison": news_comparison_payload,
                "narrative": narrative_text,
                "narrative_mode": narrative_mode,
            },
        )

        disclosures = list(DISCLOSURES) + ([DEMO_DISCLOSURE] if is_demo else [])

        fa_dict = comp.fund_a.model_dump(mode="json")
        fb_dict = comp.fund_b.model_dump(mode="json")

        return CompareResponse(
            run_id=rid,
            as_of=comp.as_of,
            funds=[fa_dict, fb_dict],
            fund_a=fa_dict,
            fund_b=fb_dict,
            criteria=[c.model_dump(mode="json") for c in comp.criteria],
            checklist={
                k: [item.model_dump(mode="json") for item in v]
                for k, v in comp.checklist.items()
            },
            scores=comp.scores,
            winner=comp.winner,
            winner_code=comp.winner_code,
            winner_name=comp.winner_name,
            ranking=comp.ranking,
            same_category=comp.same_category,
            warnings=comp.warnings,
            not_evaluated=[ne.model_dump(mode="json") for ne in comp.not_evaluated],
            narrative=narrative_text,
            narrative_mode=narrative_mode,
            disclosures=disclosures,
            citations=[f.model_dump(mode="json") for f in all_facts],
            news_comparison=news_comparison_payload,
        )
    finally:
        if closed_override:
            conn.close()


class FollowUpRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class FollowUpReplyResponse(BaseModel):
    message_id: int
    answer: str
    referenced_funds: List[dict[str, Any]] = []
    sources: List[dict[str, Any]] = []
    refused: bool = False


@app.get("/api/history")
def list_history_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    history_conn=Depends(get_history_db_conn),
):
    store = HistoryStore(history_conn)
    return store.list(limit=limit, offset=offset)


@app.get("/api/history/{run_id}")
def get_history_item_endpoint(
    run_id: str,
    history_conn=Depends(get_history_db_conn),
    conn=Depends(get_db_conn),
):
    store = HistoryStore(history_conn)
    item = store.get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Advice run '{run_id}' not found")
    try:
        from wealth_advisor.orchestration.stock360_jobs import Stock360Store
        linked = Stock360Store(conn).for_run(run_id)
    except sqlite3.Error:
        linked = []
    if isinstance(item, dict):
        item = {**item, "reports360": linked}
    return item


@app.delete("/api/history/{run_id}", status_code=204)
def delete_history_item_endpoint(
    run_id: str,
    history_conn=Depends(get_history_db_conn),
):
    store = HistoryStore(history_conn)
    deleted = store.delete(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Advice run '{run_id}' not found")
    return Response(status_code=204)


@app.post("/api/history/{run_id}/followup", response_model=FollowUpReplyResponse)
def post_followup_endpoint(
    run_id: str,
    req: FollowUpRequest,
    conn=Depends(get_db_conn),
    history_conn=Depends(get_history_db_conn),
):
    q = req.question.strip()
    if not q or len(q) > 1000:
        raise HTTPException(status_code=422, detail="Question must be between 1 and 1000 characters.")

    h_store = HistoryStore(history_conn)
    saved = h_store.get(run_id)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"Advice run '{run_id}' not found")

    prior_messages = saved.get("messages") or []
    h_store.add_message(run_id, "user", q)

    run_mode = saved.get("data_mode") or get_active_mode()
    fund_conn = conn
    closed_fund_conn = False
    if get_db_conn not in app.dependency_overrides and run_mode != get_active_mode():
        fund_conn = open_db(get_settings().active_db_path(run_mode))
        closed_fund_conn = True

    try:
        mstore = MetadataStore(fund_conn)
        if mstore.latest_nav_date() is None and run_mode == "synthetic":
            bootstrap_demo(fund_conn)

        llm_client = getattr(app.state, "llm", None) or get_llm()
        agent = FollowUpAgent(llm_client, fund_conn)
        res = agent.answer(
            run_id=run_id,
            question=q,
            saved_run=saved,
            history_messages=prior_messages,
        )
    finally:
        if closed_fund_conn:
            fund_conn.close()

    assistant_msg = h_store.add_message(
        run_id,
        "assistant",
        res.answer,
        referenced_funds=res.referenced_funds,
        sources=res.sources,
    )
    return FollowUpReplyResponse(
        message_id=assistant_msg["id"],
        answer=res.answer,
        referenced_funds=res.referenced_funds,
        sources=res.sources,
        refused=res.refused,
    )


# ── M14 & M15: Auth, Rate-Limiting, PII-Redaction Middleware, Ingestion & Monitoring Endpoints ──

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse

from wealth_advisor.agents.monitoring import AlertStore, MonitoringAgent
from wealth_advisor.audit.log import AuditLog, ProfileStore
from wealth_advisor.ingestion import (
    ConfirmationQueue,
    EncryptedDocumentStore,
    TransactionCategorizer,
    derive_financial_summary,
    parse_bank_csv,
    parse_bank_pdf,
    parse_cas_statement,
    redact_pii,
    scrub_text,
    update_profile_from_ingestion,
)
from wealth_advisor.orchestration.events import InMemoryEventBus, MarketEvent

_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)


def reset_rate_limiter() -> None:
    _rate_limit_buckets.clear()


@app.middleware("http")
async def security_and_pii_middleware(request: Request, call_next):
    # 1. Configurable API Key Authentication
    required_key = getattr(app.state, "api_key", None) or os.environ.get("WEALTH_API_KEY")
    if required_key and request.url.path.startswith("/api/"):
        auth_header = request.headers.get("Authorization", "")
        x_api_key = request.headers.get("X-API-Key", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else x_api_key.strip()
        if token != required_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: valid API key or Bearer token required"},
                headers={"X-PII-Redacted": "true"},
            )

    # 2. Configurable Rate Limiting
    env_limit = os.environ.get("WEALTH_RATE_LIMIT_PER_MIN")
    limit_per_min = (
        int(env_limit)
        if env_limit and env_limit.isdigit()
        else getattr(app.state, "rate_limit_per_minute", 120)
    )
    if limit_per_min and limit_per_min > 0 and request.url.path.startswith("/api/"):
        buckets = getattr(app.state, "rate_limit_hits", None)
        if buckets is None:
            buckets = _rate_limit_buckets
        client_id = request.headers.get("X-Client-ID") or (request.client.host if request.client else "local")
        now = time.monotonic()
        window = [t for t in buckets.get(client_id, []) if now - t < 60.0]
        if len(window) >= limit_per_min:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded; please retry later"},
                headers={"X-PII-Redacted": "true"},
            )
        window.append(now)
        buckets[client_id] = window

    response = await call_next(request)
    response.headers["X-PII-Redacted"] = "true"
    return response


def _get_event_bus() -> InMemoryEventBus:
    if not hasattr(app.state, "event_bus") or app.state.event_bus is None:
        app.state.event_bus = InMemoryEventBus()
    return app.state.event_bus


def _get_monitoring_agent() -> MonitoringAgent:
    if not hasattr(app.state, "monitoring_agent") or app.state.monitoring_agent is None:
        bus = _get_event_bus()
        alert_store = AlertStore(":memory:")
        app.state.monitoring_agent = MonitoringAgent(bus, alert_store=alert_store)
    return app.state.monitoring_agent


def _get_confirmation_queue() -> ConfirmationQueue:
    if not hasattr(app.state, "confirmation_queue") or app.state.confirmation_queue is None:
        app.state.confirmation_queue = ConfirmationQueue(":memory:")
    return app.state.confirmation_queue


def _get_encrypted_doc_store(conn: sqlite3.Connection) -> EncryptedDocumentStore:
    secret = os.environ.get("WEALTH_DOC_SECRET", "default-m14-encryption-secret")
    return EncryptedDocumentStore(conn, secret_key=secret)


class ChatRequest(BaseModel):
    message: str
    run_id: Optional[str] = None
    profile_id: str = "default_user"
    amount: Optional[float] = None
    horizon_years: Optional[float] = None
    risk_preference: Optional[Literal[
        "capital_preservation", "conservative", "moderate", "growth", "aggressive"
    ]] = None


class ChatResponse(BaseModel):
    reply: str
    redacted_message: str
    pii_redacted: List[str] = []
    goal: Optional[dict] = None
    missing_fields: List[str] = []
    follow_up_question: Optional[str] = None


@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest, conn=Depends(get_db_conn), history_conn=Depends(get_history_db_conn)):
    redacted_msg, pii_types = redact_pii(req.message)
    if req.run_id:
        h_store = HistoryStore(history_conn)
        saved = h_store.get(req.run_id)
        if saved is not None:
            llm_client = getattr(app.state, "llm", None) or get_llm()
            agent = FollowUpAgent(llm_client, conn)
            res = agent.answer(
                run_id=req.run_id,
                question=redacted_msg,
                saved_run=saved,
                history_messages=saved.get("messages") or [],
            )
            return ChatResponse(
                reply=scrub_text(res.answer),
                redacted_message=redacted_msg,
                pii_redacted=pii_types,
            )

    from wealth_advisor.agents.profiling import ProfilingAgent
    from wealth_advisor.models.enums import RiskPreference

    llm_client = getattr(app.state, "llm", None) or get_llm()
    p_agent = ProfilingAgent(llm_client)
    risk_pref = RiskPreference(req.risk_preference) if req.risk_preference else None
    prof_res = p_agent.resolve(
        redacted_msg,
        amount=req.amount,
        horizon_years=req.horizon_years,
        risk_preference=risk_pref,
    )
    if prof_res.rejected:
        raise HTTPException(status_code=400, detail=prof_res.rejected.message)
    if prof_res.missing:
        q = p_agent.follow_up_question(prof_res.missing[0])
        return ChatResponse(
            reply=q,
            redacted_message=redacted_msg,
            pii_redacted=pii_types,
            missing_fields=prof_res.missing,
            follow_up_question=q,
        )
    assert prof_res.goal is not None
    restate_str = p_agent.restate(prof_res.goal)
    return ChatResponse(
        reply=restate_str,
        redacted_message=redacted_msg,
        pii_redacted=pii_types,
        goal=prof_res.goal.model_dump(mode="json"),
    )


class StatementUploadRequest(BaseModel):
    profile_id: str = "default_user"
    statement_type: Literal["bank_csv", "bank_pdf", "cas"] = "bank_csv"
    content: str
    cas_content: Optional[str] = None
    retention_days: int = 30
    goal_amount: Optional[float] = None
    horizon_years: Optional[float] = None
    risk_preference: Optional[Literal[
        "capital_preservation", "conservative", "moderate", "growth", "aggressive"
    ]] = None


class ConfirmTransactionRequest(BaseModel):
    item_id: str
    confirmed_category: Literal[
        "salary",
        "other_income",
        "emi",
        "rent",
        "sip",
        "investment",
        "utilities",
        "discretionary",
        "transfer",
        "ambiguous",
    ]


@app.post("/api/statements/upload")
def upload_statement_endpoint(req: StatementUploadRequest, conn=Depends(get_db_conn)):
    doc_store = _get_encrypted_doc_store(conn)
    doc_record = doc_store.save(
        req.statement_type,
        req.content,
        doc_id=f"doc_{new_run_id()}",
        retention_days=req.retention_days,
    )

    queue = _get_confirmation_queue()
    categorizer = TransactionCategorizer(confirmation_queue=queue)

    bank_stmt = None
    categorized = []
    if req.statement_type == "bank_csv":
        bank_stmt = parse_bank_csv(req.content)
        categorized = categorizer.categorize_many(bank_stmt.transactions, profile_id=req.profile_id)
    elif req.statement_type == "bank_pdf":
        bank_stmt = parse_bank_pdf(req.content)
        categorized = categorizer.categorize_many(bank_stmt.transactions, profile_id=req.profile_id)

    cas_stmt = None
    if req.statement_type == "cas":
        cas_stmt = parse_cas_statement(req.content)
    elif req.cas_content:
        cas_stmt = parse_cas_statement(req.cas_content)

    summary = derive_financial_summary(bank_stmt, categorized, cas_stmt)
    p_store = ProfileStore(conn)
    audit = AuditLog(conn)

    from wealth_advisor.models.core import Goal
    from wealth_advisor.models.enums import RiskPreference

    goal = None
    if req.goal_amount and req.horizon_years and req.risk_preference:
        goal = Goal(
            amount=req.goal_amount,
            horizon_years=req.horizon_years,
            risk_preference=RiskPreference(req.risk_preference),
        )

    profile, _ = update_profile_from_ingestion(
        summary=summary,
        goal=goal,
        profile_id=req.profile_id,
        profile_store=p_store,
        audit_log=audit,
    )
    pending = queue.list_pending(profile_id=req.profile_id)

    return {
        "doc_id": doc_record.doc_id,
        "encrypted_at_rest": True,
        "expires_at": doc_record.expires_at.isoformat(),
        "profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "risk_score": profile.risk_score,
            "category": profile.category.value,
            "derived_from_statements": profile.inputs.get("derived_from_statements", {}),
        },
        "transactions_parsed": len(categorized),
        "pending_confirmations": [
            {
                "item_id": item.item_id,
                "txn_id": item.txn.txn_id,
                "description": item.txn.description,
                "amount_inr": item.txn.amount_inr,
                "suggested_category": item.suggested_category,
                "confidence": item.confidence,
            }
            for item in pending
        ],
    }


@app.get("/api/statements/confirmations")
def list_confirmations_endpoint(profile_id: Optional[str] = Query(default=None)):
    queue = _get_confirmation_queue()
    items = queue.list_pending(profile_id=profile_id)
    return {
        "pending": [
            {
                "item_id": item.item_id,
                "profile_id": item.profile_id,
                "txn_id": item.txn.txn_id,
                "description": item.txn.description,
                "amount_inr": item.txn.amount_inr,
                "suggested_category": item.suggested_category,
                "confidence": item.confidence,
                "status": item.status,
            }
            for item in items
        ]
    }


@app.post("/api/statements/confirm")
def confirm_transaction_endpoint(req: ConfirmTransactionRequest):
    queue = _get_confirmation_queue()
    try:
        updated = queue.confirm(req.item_id, req.confirmed_category)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "item_id": updated.item_id,
        "status": updated.status,
        "confirmed_category": updated.confirmed_category,
        "confidence": updated.confidence,
    }


class CreateGoalRequest(BaseModel):
    profile_id: str = "default_user"
    amount: Optional[float] = Field(default=None, gt=0)
    horizon_years: Optional[float] = Field(default=None, gt=0)
    risk_preference: Literal[
        "capital_preservation", "conservative", "moderate", "growth", "aggressive"
    ] = "moderate"
    purpose: str = ""
    excluded_categories: List[str] = []
    goals: Optional[List[dict]] = None
    total_investable: Optional[float] = None


@app.post("/api/goals")
def create_goal_endpoint(req: CreateGoalRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.agents.profiling import ProfilingAgent
    from wealth_advisor.models.core import Goal
    from wealth_advisor.models.enums import RiskPreference

    if req.goals:
        total_inv = req.total_investable or sum(float(g.get("target_amount", g.get("amount", 0.0))) for g in req.goals)
        primary = req.goals[0]
        amt = float(primary.get("target_amount", primary.get("amount", total_inv or 100_000.0)))
        hor = float(primary.get("horizon_years", 5.0))
        purp = str(primary.get("name", primary.get("purpose", req.purpose)))
    else:
        amt = float(req.amount or 100_000.0)
        hor = float(req.horizon_years or 5.0)
        purp = req.purpose
        total_inv = amt

    goal = Goal(
        amount=amt,
        horizon_years=hor,
        risk_preference=RiskPreference(req.risk_preference),
        purpose=scrub_text(purp),
        excluded_categories=req.excluded_categories,
    )
    p_store = ProfileStore(conn)
    prev = p_store.latest(req.profile_id)
    next_ver = (prev.version + 1) if prev is not None else 1
    profile, _ = ProfilingAgent.build_profile(goal, profile_id=req.profile_id, version=next_ver)

    saved_goals = req.goals if req.goals else [goal.model_dump(mode="json")]
    allocations = []
    n_goals = len(saved_goals)
    for g_item in saved_goals:
        g_name = g_item.get("name") or g_item.get("purpose") or "Goal"
        g_target = float(g_item.get("target_amount", g_item.get("amount", amt)))
        allocations.append(
            {
                "name": scrub_text(str(g_name)),
                "target_amount": g_target,
                "allocated_amount": round(total_inv / max(n_goals, 1), 2),
                "horizon_years": float(g_item.get("horizon_years", hor)),
                "priority": g_item.get("priority", "must_have"),
            }
        )

    profile.inputs["goals"] = saved_goals
    profile.inputs["allocations"] = allocations
    p_store.save(profile)
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "risk_score": profile.risk_score,
        "category": profile.category.value,
        "goal": goal.model_dump(mode="json"),
        "goals": saved_goals,
        "allocations": allocations,
    }


@app.get("/api/goals")
def get_goals_endpoint(profile_id: str = Query(default="default_user"), conn=Depends(get_db_conn)):
    p_store = ProfileStore(conn)
    latest = p_store.latest(profile_id)
    if latest is None:
        return {"profile_id": profile_id, "goals": []}
    goals_list = latest.inputs.get("goals")
    if not goals_list and latest.inputs.get("goal"):
        goals_list = [latest.inputs.get("goal")]
    return {
        "profile_id": latest.profile_id,
        "version": latest.version,
        "risk_score": latest.risk_score,
        "category": latest.category.value,
        "goals": goals_list or [],
        "allocations": latest.inputs.get("allocations", []),
    }


@app.get("/api/recommendations")
def get_recommendations_endpoint(
    limit: int = Query(default=10, ge=1, le=50),
    history_conn=Depends(get_history_db_conn),
):
    h_store = HistoryStore(history_conn)
    runs = h_store.list(limit=limit, offset=0)
    items = []
    for summary in runs.get("items", []):
        detail = h_store.get(summary["run_id"])
        if detail and detail.get("response"):
            items.append(
                {
                    "run_id": summary["run_id"],
                    "title": summary.get("title"),
                    "created_at": summary.get("created_at"),
                    "recommendations": detail["response"].get("recommendations", []),
                }
            )
    return {"items": items, "total": len(items)}


class PublishEventRequest(BaseModel):
    topic: Literal["market_data", "fund_events", "portfolio_ticks"] = "fund_events"
    event_type: str
    profile_id: str = "default_user"
    instrument_id: str = ""
    payload: dict = {}


@app.get("/api/alerts")
def get_alerts_endpoint(profile_id: Optional[str] = Query(default=None)):
    agent = _get_monitoring_agent()
    alerts = agent.get_alerts(profile_id=profile_id)
    return {
        "alerts": [a.to_dict() for a in alerts],
        "alerts_count": len(alerts),
        "suppressed_duplicates": agent.suppressed_duplicates,
        "recalibration_runs": agent.recalibration_runs,
    }


@app.post("/api/alerts/events")
def publish_alert_event_endpoint(req: PublishEventRequest):
    agent = _get_monitoring_agent()
    bus = _get_event_bus()
    before_count = len(agent.alerts)
    event = MarketEvent(
        topic=req.topic,
        event_type=req.event_type,
        profile_id=req.profile_id,
        instrument_id=req.instrument_id,
        payload=req.payload,
        timestamp=datetime.now(timezone.utc),
    )
    bus.publish(event)
    alerts = agent.get_alerts(profile_id=req.profile_id)
    created_new = len(agent.alerts) > before_count
    return {
        "event_id": event.event_id,
        "alert_emitted": created_new,
        "alerts": [a.to_dict() for a in alerts],
        "alerts_count": len(alerts),
        "suppressed_duplicates": agent.suppressed_duplicates,
        "recalibration_runs": agent.recalibration_runs,
    }


# ==============================================================================
# Stock Fundamental Analysis & Daily Sync Endpoints
# ==============================================================================

_SYMBOL_RE = re.compile(r"^[A-Z0-9&-]{1,20}$")


def _validate_stock_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper()
    if not _SYMBOL_RE.match(clean):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid NSE symbol '{symbol}'. Must be 1-20 uppercase letters, digits, '&' or '-'.",
        )
    return clean


class StockSyncRequest(BaseModel):
    force: bool = False
    mode: Literal["full", "retry_failed", "rescreen"] = "full"
    data_mode: Optional[Literal["synthetic", "real"]] = None
    sector: Optional[str] = None
    index_tag: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=5000)
    min_market_cap_cr: Optional[float] = Field(default=None, ge=0.0, le=5_000_000.0)
    min_traded_value_cr: Optional[float] = Field(default=None, ge=0.0, le=500_000.0)


class StockOverridesInput(BaseModel):
    fii_holding_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    promoter_pledge_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    nim_pct: Optional[float] = Field(default=None, ge=-20.0, le=50.0)
    gnpa_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    nnpa_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    casa_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    car_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    credit_cost_pct: Optional[float] = Field(default=None, ge=-10.0, le=50.0)
    provision_coverage_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    # 360° report manual inputs (persisted per symbol; see /api/stocks/{symbol}/manual-inputs)
    report360_inputs: Optional[dict[str, dict[str, Any]]] = None


class StockAnalyzeRequest(BaseModel):
    symbol: str
    rule_set: Optional[Literal["set1", "set2"]] = None
    stock_rule_set: Optional[Literal["set1", "set2"]] = None
    narrative: bool = True
    force_refresh: bool = False
    min_market_cap_cr: Optional[float] = Field(default=None, ge=0.0, le=5_000_000.0)
    min_traded_value_cr: Optional[float] = Field(default=None, ge=0.0, le=500_000.0)
    overrides: Optional[StockOverridesInput] = None
    bank_metrics: Optional[dict[str, float]] = None


class StockCompareRequest(BaseModel):
    symbols: Optional[List[str]] = None
    symbol_a: Optional[str] = None
    symbol_b: Optional[str] = None
    rule_set: Optional[Literal["set1", "set2"]] = None
    stock_rule_set: Optional[Literal["set1", "set2"]] = None
    narrative: bool = True
    min_market_cap_cr: Optional[float] = Field(default=None, ge=0.0, le=5_000_000.0)
    min_traded_value_cr: Optional[float] = Field(default=None, ge=0.0, le=500_000.0)
    overrides_a: Optional[StockOverridesInput] = None
    overrides_b: Optional[StockOverridesInput] = None
    overrides_by_symbol: Optional[dict[str, dict[str, float]]] = None


def _merge_stock_overrides(
    overrides: Optional[StockOverridesInput],
    bank_metrics: Optional[dict[str, float]] = None,
) -> Optional[dict[str, float]]:
    merged: dict[str, float] = {}
    if overrides is not None:
        for k, v in overrides.model_dump(exclude_none=True, exclude={"report360_inputs"}).items():
            if v is not None:
                merged[k] = float(v)
    if bank_metrics:
        for k, v in bank_metrics.items():
            if v is not None:
                merged[k] = float(v)
    return merged or None


@app.post("/api/stocks/sync", status_code=202)
def sync_stocks_endpoint(
    req: Optional[StockSyncRequest] = None,
    response: Response = None,  # type: ignore[assignment]
    conn=Depends(get_db_conn),
):
    from wealth_advisor.data.stocks.sync import StockSyncService

    body = req or StockSyncRequest()
    mode = body.data_mode or get_active_mode()
    s = get_settings()
    svc = StockSyncService(
        conn,
        data_mode=mode,
        max_rps=s.stock_sync_max_rps,
        financials_ttl_days=s.stock_financials_ttl_days,
    )
    report = svc.sync(
        force=body.force,
        mode=body.mode,
        sector=body.sector,
        index_tag=body.index_tag,
        limit=body.limit,
        min_market_cap_cr=body.min_market_cap_cr,
        min_traded_value_cr=body.min_traded_value_cr,
    )
    last_run = report.get("last_run") or {}
    job_id = last_run.get("run_id") or ""
    if report.get("sync_in_progress") or report.get("already_synced_today"):
        status_label = "already_running" if report.get("sync_in_progress") else "skipped_already_synced_today"
        raise HTTPException(
            status_code=409,
            detail={
                "status": status_label,
                "message": (
                    "Stock sync is already in progress."
                    if report.get("sync_in_progress")
                    else "Stock sync already completed today. Pass force=true to re-run."
                ),
                "run_id": job_id,
                "job_id": job_id,
            },
        )
    if response is not None:
        response.status_code = 202
    payload = dict(report)
    payload["job_id"] = job_id
    payload["run_id"] = job_id
    return payload


@app.get("/api/stocks/sync/status")
def get_stocks_sync_status_endpoint(conn=Depends(get_db_conn)):
    from wealth_advisor.data.stocks.sync import StockSyncService

    svc = StockSyncService(conn, data_mode=get_active_mode())
    st = svc.get_status()
    last = st.get("last_run") or {}
    active = st.get("active_job")
    current = active or last
    return {
        **st,
        "status": current.get("status", "idle" if not st.get("has_synced_data") else "completed"),
        "done": int(current.get("processed_symbols", st.get("total_synced", 0)) or 0),
        "total": int(current.get("total_symbols", st.get("total_synced", 0)) or 0),
        "failed": int(current.get("failed_symbols", 0) or 0),
        "passed_set1": int(current.get("set1_pass_count", st.get("set1_pass_count", 0)) or 0),
        "passed_set2": int(current.get("set2_pass_count", st.get("set2_pass_count", 0)) or 0),
        "started": current.get("started_at"),
        "finished": current.get("completed_at") or current.get("finished_at"),
        "min_market_cap_cr": current.get("min_market_cap_cr"),
        "min_traded_value_cr": current.get("min_traded_value_cr"),
    }


@app.get("/api/stocks/sync/{job_id}")
def get_stocks_sync_job_endpoint(job_id: str, conn=Depends(get_db_conn)):
    from wealth_advisor.data.stocks.sync import StockSyncService

    svc = StockSyncService(conn, data_mode=get_active_mode())
    job = svc.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Stock sync job '{job_id}' not found")
    return job


@app.get("/api/stocks/search")
@app.get("/api/stocks")
def list_stocks_endpoint(
    q: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    index_tag: Optional[str] = Query(default=None),
    rule_set: Literal["set1", "set2"] = Query(default="set2"),
    only_passing: bool = Query(default=False),
    passed_only: bool = Query(default=False),
    is_bank: Optional[bool] = Query(default=None),
    min_market_cap_cr: Optional[float] = Query(default=None, ge=0.0),
    min_traded_value_cr: Optional[float] = Query(default=None, ge=0.0),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    conn=Depends(get_db_conn),
):
    from wealth_advisor.data.stocks.sync import StockSyncService

    svc = StockSyncService(conn, data_mode=get_active_mode())
    return svc.list_stocks(
        query=q if q is not None else query,
        sector=sector,
        index_tag=index_tag,
        rule_set=rule_set,
        passed_only=bool(only_passing or passed_only),
        is_bank=is_bank,
        min_market_cap_cr=min_market_cap_cr,
        min_traded_value_cr=min_traded_value_cr,
        limit=limit,
        offset=offset,
    )


@app.get("/api/stocks/sectors")
def get_stock_sectors_endpoint(conn=Depends(get_db_conn)):
    from wealth_advisor.data.stocks.universe import list_nse_sectors

    medians = conn.execute(
        """
        SELECT sector, is_bank, peer_count, median_pe, median_pb, median_ps,
               median_ev_ebitda, median_roe, median_roce, median_debt_to_equity,
               median_nim_pct, median_gnpa_pct, median_nnpa_pct, updated_at
        FROM stock_sector_medians
        ORDER BY peer_count DESC, sector ASC
        """
    ).fetchall()
    return {
        "sectors": list_nse_sectors(),
        "sector_medians": [dict(r) for r in medians],
    }


@app.get("/api/stocks/rules")
def get_stock_rules_endpoint(rule_set: Optional[Literal["set1", "set2"]] = Query(default=None)):
    from wealth_advisor.domain.stock_rulesets import (
        compare_stock_rule_sets,
        get_stock_rules_catalog,
    )

    s = get_settings()
    if rule_set in ("set1", "set2"):
        return get_stock_rules_catalog(rule_set)
    return {
        "default": s.stock_rule_set,
        "set1": get_stock_rules_catalog("set1"),
        "set2": get_stock_rules_catalog("set2"),
        "comparison": compare_stock_rule_sets(),
    }


@app.get("/api/stocks/rules/compare")
def compare_stock_rules_endpoint():
    from wealth_advisor.domain.stock_rulesets import compare_stock_rule_sets

    return compare_stock_rule_sets()


@app.post("/api/stocks/{symbol}/refresh")
def refresh_single_stock_endpoint(symbol: str, conn=Depends(get_db_conn)):
    from wealth_advisor.data.stocks.sync import StockSyncService

    sym = _validate_stock_symbol(symbol)
    svc = StockSyncService(conn, data_mode=get_active_mode())
    bundle = svc.ensure_stock_cached(sym, force_refresh=True)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Stock '{sym}' could not be fetched")
    return {
        "status": "refreshed",
        "symbol": bundle.master.symbol,
        "name": bundle.master.name,
        "sector": bundle.master.sector,
        "as_of_date": bundle.snapshot.as_of_date,
        "price": bundle.snapshot.price,
        "market_cap_cr": bundle.snapshot.market_cap_cr,
    }


@app.post("/api/stocks/analyze")
def analyze_stock_endpoint(req: StockAnalyzeRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.agents.stock_narrator import StockNarratorAgent
    from wealth_advisor.audit.log import AuditLog
    from wealth_advisor.data.stocks.sync import StockSyncService
    from wealth_advisor.domain.stock_context import build_single_stock_analysis

    sym = _validate_stock_symbol(req.symbol)
    rid = new_run_id()
    s = get_settings()
    rs: Literal["set1", "set2"] = (
        req.stock_rule_set
        if req.stock_rule_set in ("set1", "set2")
        else (req.rule_set if req.rule_set in ("set1", "set2") else s.stock_rule_set)
    )
    svc = StockSyncService(conn, data_mode=get_active_mode(), snapshot_ttl_hours=s.stock_snapshot_ttl_hours)
    bundle = svc.ensure_stock_cached(sym, force_refresh=req.force_refresh)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Stock '{sym}' not found in cache or provider")

    peers = svc.get_sector_peers(bundle.master.sector, exclude_symbol=bundle.master.symbol, rule_set=rs, limit=4)
    ov_dict = _merge_stock_overrides(req.overrides, req.bank_metrics)
    _persist_report360_inputs(conn, bundle.master.symbol, req.overrides)

    analysis = build_single_stock_analysis(
        master=bundle.master,
        snapshot=bundle.snapshot,
        financials=bundle.financials,
        prices=bundle.prices,
        sector_median=bundle.sector_median,
        sector_peers=peers,
        rule_set=rs,
        min_market_cap_cr=req.min_market_cap_cr,
        min_traded_value_cr=req.min_traded_value_cr,
        overrides=ov_dict,
    )

    if req.narrative:
        base_llm = getattr(app.state, "llm", None) or get_llm()
        client = BudgetedLLM(base_llm, s.max_llm_calls_per_run)
        narrator = StockNarratorAgent(client)
        with trace_context(run_id=rid):
            narrative = narrator.narrate_single_stock(analysis)
        analysis["narrative"] = narrative
        flush_langfuse()
    else:
        analysis["narrative"] = ""

    audit = AuditLog(conn)
    audit.append(
        rid,
        "stock_analyzer",
        "analyze",
        {
            "symbol": sym,
            "rule_set": rs,
            "min_market_cap_cr": req.min_market_cap_cr,
            "min_traded_value_cr": req.min_traded_value_cr,
            "passed": analysis["active_screen"]["passed"],
            "composite_score": analysis["active_screen"]["composite_score"],
        },
    )
    analysis["run_id"] = rid
    return analysis


@app.get("/api/stocks/analyze/{symbol}")
def analyze_stock_get_endpoint(
    symbol: str,
    rule_set: Optional[Literal["set1", "set2"]] = Query(default=None),
    stock_rule_set: Optional[Literal["set1", "set2"]] = Query(default=None),
    narrative: bool = Query(default=True),
    min_market_cap_cr: Optional[float] = Query(default=None, ge=0.0),
    min_traded_value_cr: Optional[float] = Query(default=None, ge=0.0),
    fii_holding_pct: Optional[float] = Query(default=None, ge=0.0, le=100.0),
    promoter_pledge_pct: Optional[float] = Query(default=None, ge=0.0, le=100.0),
    conn=Depends(get_db_conn),
):
    ov = StockOverridesInput(
        fii_holding_pct=fii_holding_pct,
        promoter_pledge_pct=promoter_pledge_pct,
    )
    return analyze_stock_endpoint(
        StockAnalyzeRequest(
            symbol=symbol,
            rule_set=rule_set,
            stock_rule_set=stock_rule_set,
            narrative=narrative,
            min_market_cap_cr=min_market_cap_cr,
            min_traded_value_cr=min_traded_value_cr,
            overrides=ov,
        ),
        conn=conn,
    )


@app.post("/api/stocks/compare")
def compare_stocks_endpoint(req: StockCompareRequest, conn=Depends(get_db_conn)):
    from wealth_advisor.agents.stock_narrator import StockNarratorAgent
    from wealth_advisor.audit.log import AuditLog
    from wealth_advisor.data.stocks.sync import StockSyncService
    from wealth_advisor.domain.stock_compare import compare_stocks

    if req.symbols and len(req.symbols) >= 2:
        raw_symbols = req.symbols[:4]
    elif req.symbol_a and req.symbol_b:
        raw_symbols = [req.symbol_a, req.symbol_b]
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either symbols=[...] (2 to 4 stocks) or symbol_a and symbol_b",
        )

    clean_symbols = [_validate_stock_symbol(s) for s in raw_symbols]
    if len(set(clean_symbols)) < len(clean_symbols):
        raise HTTPException(status_code=400, detail="Please select distinct stocks to compare")

    rid = new_run_id()
    s = get_settings()
    rs: Literal["set1", "set2"] = (
        req.stock_rule_set
        if req.stock_rule_set in ("set1", "set2")
        else (req.rule_set if req.rule_set in ("set1", "set2") else s.stock_rule_set)
    )
    svc = StockSyncService(conn, data_mode=get_active_mode(), snapshot_ttl_hours=s.stock_snapshot_ttl_hours)

    bundles = []
    for sym in clean_symbols:
        b = svc.ensure_stock_cached(sym)
        if b is None:
            raise HTTPException(status_code=404, detail=f"Stock '{sym}' not found")
        bundles.append(b)

    ov_a = _merge_stock_overrides(req.overrides_a)
    ov_b = _merge_stock_overrides(req.overrides_b)
    _persist_report360_inputs(conn, bundles[0].master.symbol, req.overrides_a)
    _persist_report360_inputs(conn, bundles[1].master.symbol, req.overrides_b)

    report = compare_stocks(
        bundles,
        rule_set=rs,
        min_market_cap_cr=req.min_market_cap_cr,
        min_traded_value_cr=req.min_traded_value_cr,
        overrides_a=ov_a,
        overrides_b=ov_b,
        overrides_by_symbol=req.overrides_by_symbol,
    )
    payload = report.to_dict()

    if req.narrative:
        base_llm = getattr(app.state, "llm", None) or get_llm()
        client = BudgetedLLM(base_llm, s.max_llm_calls_per_run)
        narrator = StockNarratorAgent(client)
        with trace_context(run_id=rid):
            narrative = narrator.narrate_comparison(report)
        payload["narrative"] = narrative
        flush_langfuse()
    else:
        payload["narrative"] = ""

    audit = AuditLog(conn)
    audit.append(
        rid,
        "stock_compare",
        "compare",
        {
            "symbols": clean_symbols,
            "rule_set": rs,
            "min_market_cap_cr": req.min_market_cap_cr,
            "min_traded_value_cr": req.min_traded_value_cr,
            "winner": report.overall_winner,
        },
    )
    payload["run_id"] = rid
    return payload


# ---------------------------------------------------------------------------
# 360° stock report (on demand; see docs/plans/stock-360-analysis-plan.md)
# ---------------------------------------------------------------------------
class HoldingInput(BaseModel):
    avg_cost: float = Field(gt=0)
    buy_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    qty: float = Field(gt=0)


class ManualInput(BaseModel):
    value: Any
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}")
    source_url: str = ""


_PCT = ("num", 0.0, 100.0)
_CR = ("num", 0.0, 1e8)
MANUAL_FIELDS: dict[str, tuple] = {
    "promoter_pct": _PCT, "pledge_pct": _PCT, "fii_pct": _PCT, "dii_pct": _PCT, "public_pct": _PCT,
    "order_book_cr": _CR, "contingent_liabilities_cr": _CR, "goodwill_cr": _CR,
    "industry_cagr_pct": ("num", -50.0, 100.0), "tam_cr": _CR,
    "consensus_eps_fy1": ("num", -1e5, 1e5), "consensus_eps_fy2": ("num", -1e5, 1e5),
    "buy_count": ("num", 0.0, 200.0), "hold_count": ("num", 0.0, 200.0), "sell_count": ("num", 0.0, 200.0),
    "target_price_mean": ("num", 0.0, 1e7),
    "guidance": ("text",),
    "shareholding_history": ("history",),
}


class Report360Request(BaseModel):
    force: bool = False
    run_id: Optional[str] = None
    context: Optional[Literal["advise", "analyzer", "compare", "history"]] = None
    horizon_years: Optional[float] = Field(default=None, gt=0, le=40)
    risk_category: Optional[str] = None
    holding: Optional[HoldingInput] = None
    report360_inputs: Optional[dict[str, ManualInput]] = None
    data_mode: Optional[Literal["synthetic", "real"]] = None


class ManualInputsRequest(BaseModel):
    inputs: dict[str, ManualInput]


def _s360_runner(mode: str):
    """One job runner per (data mode, db path); tests may pre-set app.state.stock360_runner."""
    preset = getattr(app.state, "stock360_runner", None)
    if preset is not None:
        return preset
    from wealth_advisor.orchestration.stock360_jobs import Stock360JobRunner, Stock360Service

    s = get_settings()
    path = str(s.active_db_path(mode))
    runners = getattr(app.state, "stock360_runners", None)
    if runners is None:
        runners = app.state.stock360_runners = {}
    key = f"{mode}:{path}"
    if key not in runners:
        svc = Stock360Service(
            db_path=path, data_mode=mode, settings=s,
            llm_factory=lambda: getattr(app.state, "llm", None) or get_llm())
        runners[key] = Stock360JobRunner(path, svc, max_parallel=s.stock360_max_parallel_jobs,
                                         ttl_hours=s.stock360_report_ttl_hours)
    return runners[key]


def _s360_conn(runner):
    return open_db(runner.db_path)


@app.post("/api/stocks/{symbol}/report360", status_code=202)
def create_report360(symbol: str, req: Report360Request, response: Response):
    from wealth_advisor.data.stocks.report_data import ReportDataStore
    from wealth_advisor.orchestration.stock360_jobs import (
        JobConflictError, ParallelLimitError, ReportRequest,
    )

    sym = _validate_stock_symbol(symbol)
    mode = req.data_mode or get_active_mode()
    runner = _s360_runner(mode)
    if req.report360_inputs:
        for k in req.report360_inputs:
            if k not in MANUAL_FIELDS:
                raise HTTPException(status_code=422, detail=f"Unknown manual input '{k}'")
        conn = _s360_conn(runner)
        try:
            _save_manual(ReportDataStore(conn), sym, req.report360_inputs)
        finally:
            conn.close()
    rr = ReportRequest(
        symbol=sym, force=req.force, run_id=req.run_id or "", source_context=req.context or "",
        horizon_years=req.horizon_years, risk_category=req.risk_category,
        holding=req.holding.model_dump() if req.holding else None)
    try:
        sub = runner.submit(rr)
    except (JobConflictError, ParallelLimitError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if sub.kind == "cached":
        response.status_code = 200
        return {"report_id": sub.report_id, "cached": True, "job_id": None}
    return {"report_id": sub.report_id, "job_id": sub.job_id, "cached": False}


@app.get("/api/stocks/report360/jobs/{job_id}")
def get_report360_job(job_id: str, data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    from wealth_advisor.orchestration.stock360_jobs import Stock360Store

    conn = _s360_conn(_s360_runner(data_mode or get_active_mode()))
    try:
        job = Stock360Store(conn).get_job(job_id)
    finally:
        conn.close()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@app.get("/api/stocks/report360/{report_id}")
def get_report360(report_id: str, data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    from wealth_advisor.orchestration.stock360_jobs import Stock360Store

    conn = _s360_conn(_s360_runner(data_mode or get_active_mode()))
    try:
        st = Stock360Store(conn)
        meta = st.meta(report_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
        rep = st.get_report(report_id)
    finally:
        conn.close()
    return {"meta": meta, "report": rep.model_dump(mode="json") if rep else None}


@app.get("/api/stocks/{symbol}/report360/latest")
def get_report360_latest(symbol: str, data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    from wealth_advisor.orchestration.stock360_jobs import Stock360Store

    sym = _validate_stock_symbol(symbol)
    mode = data_mode or get_active_mode()
    conn = _s360_conn(_s360_runner(mode))
    try:
        st = Stock360Store(conn)
        meta = st.latest(sym, mode)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"No 360° report for '{sym}' yet")
        rep = st.get_report(meta["report_id"])
    finally:
        conn.close()
    return {"meta": meta, "report": rep.model_dump(mode="json") if rep else None}


def _persist_report360_inputs(conn, sym: str, overrides) -> None:
    inputs = getattr(overrides, "report360_inputs", None) if overrides is not None else None
    if not inputs:
        return
    from wealth_advisor.data.stocks.report_data import ReportDataStore

    _save_manual(ReportDataStore(conn), sym, inputs)


def _save_manual(store, sym: str, inputs: dict[str, Any]) -> None:
    for k, raw in inputs.items():
        mi = raw if isinstance(raw, ManualInput) else ManualInput(**raw)
        spec = MANUAL_FIELDS.get(k)
        if spec is None:
            raise HTTPException(status_code=422, detail=f"Unknown manual input '{k}'")
        if spec[0] == "num":
            try:
                val: Any = float(mi.value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"'{k}' must be a number")
            if not spec[1] <= val <= spec[2]:
                raise HTTPException(status_code=422, detail=f"'{k}' must be between {spec[1]:g} and {spec[2]:g}")
        elif spec[0] == "text":
            val = str(mi.value).strip()
            if not val or len(val) > 1000:
                raise HTTPException(status_code=422, detail=f"'{k}' must be 1-1000 characters")
        else:
            val = _clean_history(k, mi.value)
        store.set_manual(sym, k, val, mi.as_of, mi.source_url)


def _clean_history(k: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 12:
        raise HTTPException(status_code=422, detail=f"'{k}' must be a list of 1-12 quarter rows")
    out = []
    for row in value:
        if not isinstance(row, dict) or not str(row.get("quarter", "")).strip():
            raise HTTPException(status_code=422, detail=f"'{k}' rows need a 'quarter' label")
        clean: dict[str, Any] = {"quarter": str(row["quarter"])[:12]}
        for f in ("promoter_pct", "fii_pct", "dii_pct", "public_pct", "pledge_pct"):
            if row.get(f) is not None:
                try:
                    n = float(row[f])
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail=f"'{k}.{f}' must be a number")
                if not 0 <= n <= 100:
                    raise HTTPException(status_code=422, detail=f"'{k}.{f}' must be between 0 and 100")
                clean[f] = n
        out.append(clean)
    return out


@app.get("/api/stocks/{symbol}/manual-inputs")
def get_manual_inputs(symbol: str, data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    from wealth_advisor.data.stocks.report_data import ReportDataStore

    sym = _validate_stock_symbol(symbol)
    conn = _s360_conn(_s360_runner(data_mode or get_active_mode()))
    try:
        return {"symbol": sym, "fields": sorted(MANUAL_FIELDS), "inputs": ReportDataStore(conn).get_manual(sym)}
    finally:
        conn.close()


@app.put("/api/stocks/{symbol}/manual-inputs")
def put_manual_inputs(symbol: str, req: ManualInputsRequest,
                      data_mode: Optional[Literal["synthetic", "real"]] = Query(default=None)):
    from wealth_advisor.data.stocks.report_data import ReportDataStore

    sym = _validate_stock_symbol(symbol)
    for k in req.inputs:
        if k not in MANUAL_FIELDS:
            raise HTTPException(status_code=422, detail=f"Unknown manual input '{k}'")
    conn = _s360_conn(_s360_runner(data_mode or get_active_mode()))
    try:
        store = ReportDataStore(conn)
        _save_manual(store, sym, req.inputs)
        return {"symbol": sym, "inputs": store.get_manual(sym)}
    finally:
        conn.close()
