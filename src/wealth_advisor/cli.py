import argparse
import logging
import sys
from pathlib import Path

from .config import get_settings
from .logging_setup import new_run_id, setup_logging
from .models.enums import RiskPreference

log = logging.getLogger(__name__)


def _render(result) -> None:
    d = result.decision
    print(f"\nRun {result.run_id} | rule-set {result.rule_set} | profile {result.profile.profile_id} v{result.profile.version}"
          f" | risk {result.profile.risk_score:.2f} ({result.profile.category.value})")
    print("Target allocation: " + ", ".join(
        f"{b.value} {w:.1f}%" for b, w in result.target.items() if w > 0))
    print("Assumed defaults: " + ", ".join(
        f"{k}={v}" for k, v in result.assumptions.model_dump(mode="json").items() if v is not None))
    head = (f"{'Instrument':<36}{'Bucket':<8}{'Wt%':>6}{'Amount':>10}{'ER%':>6}{'Rtg':>5}"
            f"{'5yCAGR%':>9}{'Score':>7}")
    print("\n" + head + "\n" + "-" * len(head))
    for r in d.rows:
        rating = "-" if r.rating is None else f"{r.rating:g}"
        cagr = "-" if r.cagr_5y is None else f"{r.cagr_5y:.2f}"
        er = "-" if r.expense_ratio is None else f"{r.expense_ratio:.2f}"
        print(f"{r.name[:35]:<36}{r.bucket.value:<8}{r.weight:>6.1f}{r.amount:>10,.0f}"
              f"{er:>6}{rating:>5}{cagr:>9}{r.score:>7.1f}")
        print(f"    why: {r.rationale}")
        print(f"    cites: {', '.join(r.citation_ids)}")
    if d.unallocated_weight > 0.01:
        print(f"\nUnallocated: {d.unallocated_weight:.1f}% of the portfolio (no eligible fund)")
    for e in d.escalations:
        print(f"ESCALATE: {e}")
    print("\n" + "\n".join(f"* {t}" for t in result.compliance.disclosures))


def _get_goal(
    llm,
    text: str,
    assume_yes: bool,
    *,
    amount: float | None = None,
    horizon_years: float | None = None,
    risk_preference: str | None = None,
    purpose: str | None = None,
    excluded_categories: list[str] | None = None,
):
    from .agents.profiling import ProfilingAgent
    from .models.enums import RiskPreference

    agent = ProfilingAgent(llm)
    interactive = sys.stdin.isatty() and not assume_yes
    risk_enum = RiskPreference(risk_preference) if risk_preference else None
    while True:
        res = agent.resolve(
            text,
            amount=amount,
            horizon_years=horizon_years,
            risk_preference=risk_enum,
            purpose=purpose,
            excluded_categories=excluded_categories,
        )
        if res.rejected:
            print(res.rejected.message)
            return None
        if res.missing:
            if not interactive:
                print("Missing: " + ", ".join(res.missing))
                return None
            text = (text + " " + input(agent.follow_up_question(res.missing[0]) + " ")).strip()
            continue
        assert res.goal is not None
        print("\nI understood: " + agent.restate(res.goal))
        if not interactive:
            return res.goal
        ans = input("Proceed? [y/n/edit] ").strip().lower()
        if ans in ("y", "yes", ""):
            return res.goal
        if ans in ("n", "no"):
            return None
        text = input("Describe your goal again: ")
        amount = None
        horizon_years = None
        risk_enum = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wealth-advisor")
    parser.add_argument("--db", type=Path, help="SQLite file override")
    parser.add_argument(
        "--mode",
        choices=["synthetic", "real"],
        help="Data mode: synthetic or real (default: WEALTH_DATA_MODE)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hello", help="Smoke test")
    llm = sub.add_parser("llm-ping", help="Real LLM call (cached, traced in Langfuse)")
    llm.add_argument("prompt", nargs="?", default="Say hello in five words.")
    llm.add_argument("--no-cache", action="store_true", help="bypass on-disk LLM cache")
    sub.add_parser("seed-demo", help="Load SYNTHETIC demo funds, NAVs, stats and fact sheets")
    sr = sub.add_parser("seed-real", help="Load REAL curated funds, fetch mfapi/AMFI NAVs, stats and fact sheets")
    sr.add_argument("--csv", type=Path, help="Curated funds CSV (default: data/curated_funds.csv)")
    sr.add_argument(
        "--provider",
        choices=["mfapi_amfi", "amfi_only", "yfinance"],
        help="Market data provider (default: WEALTH_REAL_DATA_PROVIDER)",
    )
    sr.add_argument("--refresh-amfi", action="store_true", help="Also fetch latest AMFI NAVAll.txt")
    fn = sub.add_parser("funds", help="Query funds with exact filters")
    fn.add_argument("--category")
    fn.add_argument("--max-expense", type=float)
    fn.add_argument("--min-aum", type=float)
    fn.add_argument("--min-rating", type=float)
    fn.add_argument("--plan", choices=["direct", "regular"])
    am = sub.add_parser("ingest-amfi", help="Load NAVs from an AMFI NAVAll.txt (file or download)")
    am.add_argument("--file", type=Path)
    cv = sub.add_parser("load-funds", help="Load curated fund metadata CSV")
    cv.add_argument("csv", type=Path)
    ad = sub.add_parser("advise", help="Structured, hybrid, or natural-language query -> cited mutual fund shortlist")
    ad.add_argument("query", nargs="?", default="", help="Optional natural-language goal or extra notes")
    ad.add_argument("--amount", type=float, help="Investment amount in INR (e.g. 50000)")
    ad.add_argument("--horizon", type=float, help="Investment horizon in years (e.g. 5)")
    ad.add_argument(
        "--risk",
        choices=["capital_preservation", "conservative", "moderate", "growth", "aggressive"],
        help="Risk preference taxonomy",
    )
    ad.add_argument("--purpose", default=None, help="Optional goal purpose (e.g. retirement)")
    ad.add_argument("--exclude", nargs="*", default=None, help="Fund categories to exclude")
    ad.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompts")
    ad.add_argument("--no-cache", action="store_true", help="bypass on-disk LLM cache")
    ad.add_argument("--rule-set", choices=["set1", "set2", "combined"], help="Fund selection rule set (default: set1)")
    ad.add_argument("--include-stocks", action="store_true", help="Include Direct Equity stock sleeve in advice")
    ad.add_argument("--stock-rule-set", choices=["set1", "set2"], help="Stock screening rule set (default: set2)")
    ad.add_argument(
        "--min-market-cap",
        "--min-market-cap-cr",
        dest="min_market_cap",
        type=float,
        default=None,
        help="Optional min market cap in ₹ Cr (default: no limit)",
    )
    ad.add_argument(
        "--min-traded-value",
        "--min-traded-value-cr",
        dest="min_traded_value",
        type=float,
        default=None,
        help="Optional min avg daily traded value in ₹ Cr",
    )
    ru = sub.add_parser("rules", help="View fund-selection rule catalog and comparisons")
    ru.add_argument("--rule-set", choices=["set1", "set2", "combined"], help="Describe specific rule set")
    ru.add_argument("--compare", action="store_true", help="Compare all rule sets side-by-side")
    ss = sub.add_parser("sync-schemes", help="Sync AMFI scheme master, plus official AMFI TER and quarterly AUM")
    ss.add_argument("--file", type=Path, help="Optional local AMFI NAVAll.txt file")
    ss.add_argument("--ter-file", type=Path, help="Optional AMFI TER CSV/HTML/XLSX/JSON export file")
    ss.add_argument("--aum-file", type=Path, help="Optional AMFI quarterly average AUM CSV/HTML/XLSX/JSON export file")
    ss.add_argument("--skip-ter-aum", action="store_true", help="Skip automatic live AMFI TER and AUM download")
    cmp_p = sub.add_parser("compare", help="Compare two mutual funds head-to-head")
    cmp_p.add_argument("code_a", help="First fund scheme code")
    cmp_p.add_argument("code_b", help="Second fund scheme code")
    cmp_p.add_argument("--no-narrative", action="store_true", help="Skip LLM narrative verdict")
    rp = sub.add_parser("replay", help="Re-run deterministic steps of a logged run and diff")
    rp.add_argument("run_id")
    sub.add_parser("runs", help="List audited runs")
    sub.add_parser("audit-verify", help="Verify the audit log hash chain")

    up_p = sub.add_parser("upload-portfolio", help="Upload a portfolio CSV, valuate holdings, and check 5/25 drift")
    up_p.add_argument("csv", type=Path, help="Path to portfolio CSV (scheme/isin, units, avg_cost, as_of)")
    up_p.add_argument("--profile-id", help="Optional existing profile_id to update (increments version)")
    up_p.add_argument("--horizon", type=float, default=7.0, help="Goal horizon in years if creating a new profile")
    up_p.add_argument("--risk", choices=[r.value for r in RiskPreference], default="moderate")

    dr_p = sub.add_parser("daily-run", help="Run the 08:00 IST daily recalibration cycle")
    dr_p.add_argument("--profile-id", help="Specific profile_id (defaults to all active positions)")
    dr_p.add_argument("--as-of", help="Optional ISO date (YYYY-MM-DD) for valuation/evaluation")
    dr_p.add_argument("--confirm", action="store_true", help="Auto-confirm any recommended rebalance")

    sim_p = sub.add_parser("simulate-daily", help="Simulate N days of the daily recalibration loop")
    sim_p.add_argument("--profile-id", required=True, help="Active profile_id to simulate")
    sim_p.add_argument("--days", type=int, default=30, help="Number of days to simulate (default: 30)")

    cf_p = sub.add_parser("confirm-rebalance", help="Confirm a pending recommend-only rebalance proposal")
    cf_p.add_argument("profile_id", help="Profile ID whose pending rebalance should be confirmed")

    st_p = sub.add_parser("stocks", help="List/filter synced NSE stocks or manage stock sync/universe")
    st_p.add_argument("--rule-set", choices=["set1", "set2"], default="set2", help="Stock screening rule set (default: set2)")
    st_p.add_argument("--passed-only", "--only-passing", dest="passed_only", action="store_true", help="Show only stocks passing the active rule set")
    st_p.add_argument("--sector", default=None, help="Filter by sector")
    st_p.add_argument("--q", "--query", dest="query", default=None, help="Search by symbol or company name")
    st_p.add_argument("--min-market-cap", "--min-market-cap-cr", dest="min_market_cap", type=float, default=None, help="Optional min market cap filter in ₹ Cr")
    st_p.add_argument("--min-traded-value", "--min-traded-value-cr", dest="min_traded_value", type=float, default=None, help="Optional min avg daily traded value filter in ₹ Cr")
    st_p.add_argument("--limit", type=int, default=50, help="Max rows to display (default: 50)")
    st_sub = st_p.add_subparsers(dest="stocks_cmd", required=False)
    st_sync = st_sub.add_parser("sync", help="Run daily stock sync, sector median computation, and Set 1/Set 2 screening")
    st_sync.add_argument("--mode", "--sync-mode", dest="sync_mode", choices=["full", "retry_failed", "rescreen"], default="full", help="Sync mode")
    st_sync.add_argument("--force", action="store_true", help="Bypass once-per-day guard")
    st_sync.add_argument("--min-market-cap", "--min-market-cap-cr", dest="min_market_cap", type=float, default=None, help="Optional min market cap filter in ₹ Cr")
    st_sync.add_argument("--min-traded-value", "--min-traded-value-cr", dest="min_traded_value", type=float, default=None, help="Optional min avg daily traded value filter in ₹ Cr")
    st_sync.add_argument("--sector", default=None, help="Optional sector filter")
    st_sync.add_argument("--index-tag", default=None, help="Optional index tag filter")
    st_sync.add_argument("--limit", type=int, default=None, help="Optional max symbols to sync")
    st_ref = st_sub.add_parser("refresh-universe", help="Download latest NSE EQUITY_L.csv and update bundled universe")
    st_ref.add_argument("--url", default=None, help="Optional override URL for NSE EQUITY_L.csv")

    ss_p = sub.add_parser("sync-stocks", help="Run daily stock sync, sector median computation, and Set 1/Set 2 screening")
    ss_p.add_argument("--sync-mode", "--mode", dest="sync_mode", choices=["full", "retry_failed", "rescreen"], default="full", help="Sync mode (full, retry_failed, rescreen)")
    ss_p.add_argument("--force", action="store_true", help="Bypass once-per-day guard")
    ss_p.add_argument("--min-market-cap", "--min-market-cap-cr", dest="min_market_cap", type=float, default=None, help="Optional min market cap filter in ₹ Cr")
    ss_p.add_argument("--min-traded-value", "--min-traded-value-cr", dest="min_traded_value", type=float, default=None, help="Optional min avg daily traded value filter in ₹ Cr")
    ss_p.add_argument("--sector", default=None, help="Optional sector filter")
    ss_p.add_argument("--index-tag", default=None, help="Optional index tag filter")
    ss_p.add_argument("--limit", type=int, default=None, help="Optional max symbols to sync")

    an_p = sub.add_parser("analyze-stock", help="Run single-stock fundamental analysis (5Y statements, ratios, valuation, Set 1/2 screen)")
    an_p.add_argument("symbol", help="NSE stock symbol (e.g. TCS, HDFCBANK)")
    an_p.add_argument("--rule-set", "--stock-rule-set", dest="rule_set", choices=["set1", "set2"], default="set2", help="Stock screening rule set (default: set2)")
    an_p.add_argument("--min-market-cap", "--min-market-cap-cr", dest="min_market_cap", type=float, default=None, help="Optional min market cap threshold in ₹ Cr (activates 10th pointer in Set 2)")
    an_p.add_argument("--min-traded-value", "--min-traded-value-cr", dest="min_traded_value", type=float, default=None, help="Optional min daily traded value threshold in ₹ Cr")
    an_p.add_argument("--no-narrative", action="store_true", help="Skip LLM analyst memo")
    an_p.add_argument("--refresh", action="store_true", help="Force refresh stock snapshot and financials")

    cs_p = sub.add_parser("compare-stocks", help="Compare 2 to 4 NSE stocks head-to-head")
    cs_p.add_argument("symbols", nargs="+", help="2 to 4 NSE stock symbols (e.g. TCS INFY WIPRO)")
    cs_p.add_argument("--rule-set", "--stock-rule-set", dest="rule_set", choices=["set1", "set2"], default="set2", help="Stock screening rule set (default: set2)")
    cs_p.add_argument("--min-market-cap", "--min-market-cap-cr", dest="min_market_cap", type=float, default=None, help="Optional min market cap threshold in ₹ Cr")
    cs_p.add_argument("--min-traded-value", "--min-traded-value-cr", dest="min_traded_value", type=float, default=None, help="Optional min daily traded value threshold in ₹ Cr")
    cs_p.add_argument("--no-narrative", action="store_true", help="Skip LLM comparative verdict")

    sr_p = sub.add_parser("stock-rules", help="View stock fundamental screening and comparison rules (Set 1 & Set 2)")
    sr_p.add_argument("--rule-set", choices=["set1", "set2", "all"], default="all", help="Rule set to inspect (default: all)")
    sr_p.add_argument("--compare", action="store_true", help="Compare Set 1 vs Set 2 side-by-side")
    args = parser.parse_args(argv)


    s = get_settings()
    setup_logging(s.log_level, log_dir=s.log_dir, log_file="cli.log")
    rid = new_run_id()

    if args.cmd == "hello":
        print(f"wealth-advisor ready (run_id={rid})")
        return 0
    if args.cmd == "llm-ping":
        from .llm import CachedLLM, flush_langfuse, trace_context
        from .llm.factory import get_llm

        client = get_llm(cached=False if args.no_cache else None)
        with trace_context(run_id=rid, step="llm-ping"):
            print(client.complete([{"role": "user", "content": args.prompt}]))
        flush_langfuse()
        if isinstance(client, CachedLLM):
            log.info("cache hits=%d misses=%d", client.hits, client.misses)
        return 0

    from .audit.log import AuditLog
    from .data.store import MetadataStore
    from .db import open_db

    default_mode = args.mode
    if default_mode is None and args.cmd in ("seed-real", "sync-schemes"):
        default_mode = "real"
    elif default_mode is None and args.cmd == "seed-demo":
        default_mode = "synthetic"
    db_file = args.db or s.active_db_path(default_mode)
    conn = open_db(db_file)
    store = MetadataStore(conn)

    if args.cmd == "seed-demo":
        from .bootstrap import bootstrap_demo

        print(f"Seeded {bootstrap_demo(conn)} synthetic demo funds into {db_file}")
    elif args.cmd == "seed-real":
        from .bootstrap import bootstrap_real

        rep = bootstrap_real(
            conn,
            csv_path=args.csv,
            provider_name=args.provider,
            refresh_amfi=args.refresh_amfi,
        )
        print(
            f"Synced {rep.schemes_synced}/{rep.funds_loaded} real funds "
            f"({rep.nav_points_saved} NAV points, as_of={rep.latest_nav_date}) into {db_file}"
        )
        if rep.errors:
            print(f"Warnings/errors ({len(rep.errors)}):")
            for err in rep.errors:
                print(f"  - {err}")

    elif args.cmd == "funds":
        rows = store.query_funds(
            category=args.category, max_expense=args.max_expense, min_aum=args.min_aum,
            min_rating=args.min_rating, plan=args.plan)
        print(f"{'Code':<10}{'Name':<40}{'Category':<20}{'ER%':>6}{'AUM Cr':>9}{'Rtg':>5}"
              "  source (as_of)")
        for f in rows:
            er = "-" if f.expense_ratio is None else f"{f.expense_ratio:.2f}"
            aum = "-" if f.aum_cr is None else f"{f.aum_cr:,.0f}"
            rt = "-" if f.rating is None else f"{f.rating:g}"
            print(f"{f.code:<10}{f.name[:39]:<40}{f.category:<20}{er:>6}{aum:>9}{rt:>5}  "
                  f"{f.source} ({f.as_of})")
        print(f"{len(rows)} fund(s)")
    elif args.cmd == "ingest-amfi":
        from .data.amfi import fetch_nav_all, load_nav_all

        text = args.file.read_text() if args.file else fetch_nav_all()
        print(load_nav_all(store, text).model_dump_json(indent=2))
    elif args.cmd == "sync-schemes":
        from .data.amfi import fetch_nav_all, sync_scheme_master
        from .data.amfi_aum import load_aum_file, sync_latest_aum
        from .data.amfi_ter import load_ter_file, sync_latest_ter

        text = args.file.read_text(encoding="utf-8", errors="replace") if args.file else fetch_nav_all()
        count, rejected = sync_scheme_master(store, text)
        print(
            f"Synced {count} open-ended schemes into scheme_master "
            f"({len(rejected)} rejected rows, db={db_file})"
        )
        if args.ter_file:
            ter_rep = load_ter_file(store, args.ter_file)
            print(
                f"TER import: {ter_rep.rows_matched} schemes updated "
                f"(rows_parsed={ter_rep.rows_parsed}, unmatched={len(ter_rep.unmatched)})"
            )
        elif not args.file and not args.skip_ter_aum:
            try:
                ter_rep = sync_latest_ter(store)
                print(
                    f"AMFI live TER sync: {ter_rep.rows_matched} schemes updated "
                    f"(rows_parsed={ter_rep.rows_parsed}, unmatched={len(ter_rep.unmatched)})"
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Automatic AMFI TER fetch failed (%s); falling back to defaults", exc)
                print(f"Warning: Automatic AMFI TER fetch failed ({exc}); using fallback metadata.")

        if args.aum_file:
            aum_rep = load_aum_file(store, args.aum_file)
            print(
                f"AUM import: {aum_rep.rows_matched} schemes updated "
                f"(rows_parsed={aum_rep.rows_parsed}, unmatched={len(aum_rep.unmatched)})"
            )
        elif not args.file and not args.skip_ter_aum:
            try:
                aum_rep = sync_latest_aum(store)
                print(
                    f"AMFI live AUM sync: {aum_rep.rows_matched} schemes updated "
                    f"(rows_parsed={aum_rep.rows_parsed}, unmatched={len(aum_rep.unmatched)})"
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Automatic AMFI AUM fetch failed (%s); falling back to defaults", exc)
                print(f"Warning: Automatic AMFI AUM fetch failed ({exc}); using fallback metadata.")
    elif args.cmd == "compare":
        from statistics import median
        from .agents.compare_narrator import CompareNarrator, comparison_facts
        from .audit.log import sha256_json
        from .data.on_demand import compute_fund_profile
        from .domain.fund_compare import compare_funds
        from .guardrails.disclosures import DEMO_DISCLOSURE, DISCLOSURES
        from .llm import flush_langfuse, trace_context
        from .llm.factory import get_llm
        from .models.enums import Bucket

        active_mode = default_mode or s.data_mode
        code_a = args.code_a.strip()
        code_b = args.code_b.strip()
        if code_a == code_b:
            print("Error: Please provide two distinct fund codes.")
            return 2
        try:
            pa = compute_fund_profile(store, code_a, fetch_if_missing=True)
            pb = compute_fund_profile(store, code_b, fetch_if_missing=True)
        except Exception as exc:
            print(f"Error loading fund profile: {exc}")
            return 2

        peer_median_ytm: float | None = None
        if pa.bucket == Bucket.DEBT or pb.bucket == Bucket.DEBT:
            debt_ytms = [
                f.ytm for f in store.query_funds(bucket=Bucket.DEBT) if f.ytm is not None
            ]
            if debt_ytms:
                peer_median_ytm = float(median(debt_ytms))

        res = compare_funds(pa, pb, peer_median_ytm=peer_median_ytm)
        llm_client = None
        if not args.no_narrative:
            try:
                llm_client = get_llm()
            except Exception as exc:
                log.warning("CompareNarrator LLM unavailable (%s); using template fallback", exc)
                llm_client = None

        with trace_context(run_id=rid, step="compare_narrator"):
            narrative_text, narrative_mode = CompareNarrator(llm_client).narrate(
                res, narrative=not args.no_narrative
            )
        flush_langfuse()

        all_facts = comparison_facts(res)
        fact_hashes = {f.id: sha256_json(f.model_dump(mode="json")) for f in all_facts}

        AuditLog(conn).append(
            rid,
            agent="fund_compare",
            step="compare",
            model=getattr(llm_client, "model", "") if (not args.no_narrative and llm_client) else "deterministic",
            payload={
                "inputs": {
                    "codes": [code_a, code_b],
                    "narrative": not args.no_narrative,
                    "data_mode": active_mode,
                },
                "fact_ids": [f.id for f in all_facts],
                "fact_hashes": fact_hashes,
                "result": res.model_dump(mode="json"),
                "narrative": narrative_text,
                "narrative_mode": narrative_mode,
            },
        )

        print(f"\nRun {rid} | Compare {pa.code} vs {pb.code}")
        print(f"  Fund A: {pa.name} ({pa.code}) [{pa.category} | {pa.plan or '-'} | {pa.option or '-'}]")
        print(f"  Fund B: {pb.name} ({pb.code}) [{pb.category} | {pb.plan or '-'} | {pb.option or '-'}]")
        for w in res.warnings:
            print(f"\nWARNING: {w}")
        print(
            f"\nOverall Scores: {pa.code} = {res.scores.get(pa.code, 0.0):.1f}/100 | "
            f"{pb.code} = {res.scores.get(pb.code, 0.0):.1f}/100 | "
            f"Ranking: {' > '.join(res.ranking)}"
        )
        head = f"{'Criterion':<28}{'Ref':<14}{pa.code:>14}{pb.code:>14}{'Winner':>10}{'Status':>16}"
        print("\n" + head + "\n" + "-" * len(head))
        for c in res.criteria:
            va = c.value_a
            vb = c.value_b
            sa = "-" if va is None else (f"{va:.2f}{c.unit}" if isinstance(va, float) else f"{va}{c.unit}")
            sb = "-" if vb is None else (f"{vb:.2f}{c.unit}" if isinstance(vb, float) else f"{vb}{c.unit}")
            w = c.winner or "-"
            print(f"{c.label[:27]:<28}{c.session_ref[:13]:<14}{sa:>14}{sb:>14}{w:>10}{c.status:>16}")

        print("\nChecklist Flags:")
        for p in (pa, pb):
            flags = res.checklist.get(p.code, [])
            bad = [f for f in flags if f.status == "flag"]
            status_txt = "No red flags" if not bad else "; ".join(f"{f.label} ({f.detail})" for f in bad)
            print(f"  {p.code}: {status_txt}")

        print("\nNot Evaluated (requires holdings/offline data):")
        for ne in res.not_evaluated:
            print(f"  - {ne.label} ({ne.session_ref}): {ne.reason}")

        if narrative_text:
            print(f"\nVerdict ({narrative_mode}):\n  {narrative_text}")

        is_demo = code_a.startswith("DEMO_") or code_b.startswith("DEMO_") or active_mode == "synthetic"
        disclosures = list(DISCLOSURES) + ([DEMO_DISCLOSURE] if is_demo else [])
        print("\n" + "\n".join(f"* {t}" for t in disclosures))
    elif args.cmd == "load-funds":
        from .data.store import load_funds_csv

        records, errors = load_funds_csv(args.csv)
        store.add_funds(records)
        print(f"Loaded {len(records)} funds; {len(errors)} row error(s)")
        for e in errors:
            print("  " + e)
    elif args.cmd == "advise":
        from .guardrails.suitability import SuitabilityError
        from .llm import flush_langfuse, trace_context
        from .llm.factory import get_llm
        from .pipeline import advise

        client = get_llm(cached=False if args.no_cache else None)
        with trace_context(run_id=rid):
            goal = _get_goal(
                client,
                args.query,
                args.yes,
                amount=args.amount,
                horizon_years=args.horizon,
                risk_preference=args.risk,
                purpose=args.purpose,
                excluded_categories=args.exclude,
            )
        if goal is None:
            flush_langfuse()
            return 2
        try:
            _render(
                advise(
                    goal,
                    client,
                    conn,
                    run_id=rid,
                    rule_set=args.rule_set,
                    include_stocks=bool(getattr(args, "include_stocks", False)),
                    stock_rule_set=getattr(args, "stock_rule_set", None),
                    min_market_cap_cr=getattr(args, "min_market_cap", None),
                    min_traded_value_cr=getattr(args, "min_traded_value", None),
                )
            )
        except SuitabilityError as exc:
            print(f"Blocked by compliance: {exc}")
            return 3
        except RuntimeError as exc:
            print(exc)
            return 2
        finally:
            flush_langfuse()
    elif args.cmd == "rules":
        from .domain.rule_catalog import compare_rule_sets, describe_rule_set
        from .domain.rulesets import active_rule_set

        if args.compare:
            rows = compare_rule_sets()
            print(f"\n{'Rule':<32}{'Set 1':<22}{'Set 2':<22}{'Combined':<22}{'Strategy':<18}{'Winner'}")
            print("-" * 115)
            for r in rows:
                title = r['title'][:30]
                v1 = str(r['set1_value'])[:20]
                v2 = str(r['set2_value'])[:20]
                vc = str(r['combined_value'])[:20]
                strat = r['strategy'][:16]
                winner = r['winner'][:10]
                print(f"{title:<32}{v1:<22}{v2:<22}{vc:<22}{strat:<18}{winner}")
        else:
            target = args.rule_set or active_rule_set()
            desc = describe_rule_set(target)
            print("\n" + "=" * 80)
            print(f"Rule Set: {desc['label']} ({desc['rule_set']})")
            print(f"Description: {desc['description']}")
            print("=" * 80)
            for g in desc['groups']:
                print(f"\n--- {g['name']} ---")
                for r in g['rules']:
                    used_badge = "[ACTIVE]" if r['used'] else "[OFF]"
                    kind_badge = f"[{r['kind'].upper()}]"
                    print(f"\n* {r['title']} {kind_badge} {used_badge}")
                    print(f"  Current Value : {r['value']}")
                    print(f"  Description   : {r['description']}")
                    print(f"  Live Example  : {r['example']}")
                    print(f"  Source Ref    : {r['source_ref']}")
                    if "provenance" in r:
                        p = r['provenance']
                        print(f"  Provenance    : {p['strategy']} (Winner: {p['winner']}) - {p['reason']}")
            if "provenance_summary" in desc:
                print(f"\n--- Combined Merge Provenance ({len(desc['provenance_summary'])} rules) ---")
                for p in desc['provenance_summary']:
                    flag = " [CONFLICT RESOLVED]" if p.get('conflict') else ""
                    print(f"  • {p['setting']}: {p['strategy']} -> {p['winner']} ({p['reason']}){flag}")
        return 0
    elif args.cmd == "replay":
        from .audit.replay import replay

        rep = replay(AuditLog(conn), args.run_id)
        for c in rep.checks:
            print(f"{'OK  ' if c.ok else 'DIFF'} {c.name}")
            if not c.ok:
                print(f"     stored={c.expected}\n     replay={c.actual}")
        print("REPLAY " + ("MATCHES" if rep.ok else "FAILED (or run not found)"))
        return 0 if rep.ok else 1
    elif args.cmd == "runs":
        for r in AuditLog(conn).run_ids():
            print(r)
    elif args.cmd == "audit-verify":
        ok, bad = AuditLog(conn).verify()
        print("audit chain OK" if ok else f"audit chain BROKEN at seq {bad}")
        return 0 if ok else 1
    elif args.cmd == "upload-portfolio":
        from .audit.log import ProfileStore
        from .domain.allocation import target_allocation
        from .models.core import Goal
        from .portfolio import (
            PortfolioStore,
            analyze_portfolio_drift,
            parse_portfolio_csv,
            update_profile_from_portfolio,
            valuate_portfolio,
        )

        parsed = parse_portfolio_csv(args.csv, store)
        if parsed.errors:
            print(f"Portfolio CSV has {len(parsed.errors)} row error(s):")
            for err in parsed.errors:
                print(f"  row {err.row} [{err.field}={err.value!r}]: {err.message}")
            if not parsed.holdings:
                return 2
        val = valuate_portfolio(parsed.holdings, store)
        prev_prof = ProfileStore(conn).latest(args.profile_id) if args.profile_id else None
        goal = Goal(
            amount=max(val.total_value, 10_000.0),
            horizon_years=args.horizon,
            risk_preference=RiskPreference(args.risk),
        )
        new_prof, _ = update_profile_from_portfolio(
            conn, val, goal, prev_profile=prev_prof, run_id=rid
        )
        PortfolioStore(conn).save(new_prof.profile_id, parsed.holdings, val)
        target = target_allocation(new_prof.allocation_band)
        drift_rep = analyze_portfolio_drift(val, target, s=s)
        print(
            f"Valued {len(val.holdings)} holding(s) as of {val.as_of}: "
            f"total=Rs {val.total_value:,.2f} (cost=Rs {val.total_cost:,.2f}, HHI={val.hhi:.4f})"
        )
        print(
            f"Profile {new_prof.profile_id} v{new_prof.version}: "
            f"risk_score={new_prof.risk_score} ({new_prof.category.value})"
        )
        for d in drift_rep.drift_items:
            status = "BREACHED" if d.breached else "OK"
            print(
                f"  {d.bucket.value:<14} current={d.current:5.1f}%  target={d.target:5.1f}%  "
                f"abs={d.abs_drift:+5.1f}pp  rel={d.rel_drift:5.1f}%  [{status}]"
            )
        for t in drift_rep.trades:
            print(
                f"  Trade [{t.action.value}] {t.name}: Rs {t.current_amount:,.0f} -> "
                f"Rs {t.target_amount:,.0f} ({t.amount_delta:+,.0f})"
            )
            for w in t.warnings:
                print(f"    ! {w}")
    elif args.cmd == "daily-run":
        from datetime import date as _date

        from .daily import DailyScheduler, run_daily_cycle
        from .llm.factory import get_llm
        from .notifications import ConsoleNotifier

        client = get_llm(cached=False if args.no_cache else None)
        notifier = ConsoleNotifier(printer=print)
        as_of_dt = _date.fromisoformat(args.as_of) if args.as_of else None
        if args.profile_id:
            res = run_daily_cycle(
                conn,
                client,
                profile_id=args.profile_id,
                as_of=as_of_dt,
                notifier=notifier,
                refresh_nav_stats=True,
                auto_confirm=args.confirm,
            )
            print(f"[{res.as_of}] {res.profile_id}: {res.outcome.value} — {res.message}")
        else:
            sched = DailyScheduler(conn, client, notifier=notifier)
            results = sched.run_all(as_of=as_of_dt, auto_confirm=args.confirm)
            for r in results:
                print(f"[{r.as_of}] {r.profile_id}: {r.outcome.value} — {r.message}")
    elif args.cmd == "simulate-daily":
        from .daily import simulate_daily_loop
        from .llm.factory import get_llm
        from .notifications import ConsoleNotifier

        client = get_llm(cached=False if args.no_cache else None)
        rep = simulate_daily_loop(
            conn,
            client,
            profile_id=args.profile_id,
            days=args.days,
            notifier=ConsoleNotifier(quiet=True),
        )
        print(
            f"Simulated {rep.total_days} days ({rep.start_date} .. {rep.end_date}): "
            f"HOLD={rep.hold_count}, REBALANCE={rep.rebalance_count}, "
            f"ESCALATE={rep.escalate_count}, cache_hits={rep.cache_hits}"
        )
    elif args.cmd == "confirm-rebalance":
        from .daily import confirm_rebalance

        pos = confirm_rebalance(conn, args.profile_id)
        print(
            f"Confirmed rebalance for {pos.profile_id} as of {pos.last_rebalance_date} "
            f"({len(pos.rows)} active position(s))"
        )
    elif args.cmd in ("stocks", "sync-stocks"):
        from .data.stocks.sync import StockSyncService

        active_mode = default_mode or s.data_mode
        if args.cmd == "stocks" and getattr(args, "stocks_cmd", None) == "refresh-universe":
            from .data.stocks.universe import BUNDLED_NSE_CSV, NSE_EQUITY_L_URL, refresh_nse_universe

            items = refresh_nse_universe(
                url=args.url or NSE_EQUITY_L_URL,
                save_to_path=BUNDLED_NSE_CSV,
            )
            print(f"Refreshed NSE equity universe: {len(items)} active EQ symbols saved to {BUNDLED_NSE_CSV}")
        elif args.cmd == "sync-stocks" or (args.cmd == "stocks" and getattr(args, "stocks_cmd", None) == "sync"):
            svc = StockSyncService(
                conn,
                data_mode=active_mode,
                max_rps=s.stock_sync_max_rps,
                financials_ttl_days=s.stock_financials_ttl_days,
            )
            rep = svc.sync(
                mode=getattr(args, "sync_mode", "full"),
                force=args.force,
                sector=args.sector,
                index_tag=getattr(args, "index_tag", None),
                limit=args.limit,
                min_market_cap_cr=args.min_market_cap,
                min_traded_value_cr=args.min_traded_value,
            )
            if rep.get("already_synced_today"):
                print("Stock sync skipped: already synced today (use --force to re-run).")
            elif rep.get("sync_in_progress"):
                print("Stock sync skipped: another sync is currently in progress.")
            else:
                lr = rep.get("last_run") or {}
                print(
                    f"Stock sync completed (mode={lr.get('mode')}, data_mode={lr.get('data_mode')}): "
                    f"{rep.get('total_synced', 0)} synced, "
                    f"{lr.get('failed_symbols', 0)} failed, "
                    f"Set 1 pass={rep.get('set1_pass_count', 0)}, "
                    f"Set 2 pass={rep.get('set2_pass_count', 0)}"
                )
        else:
            svc = StockSyncService(conn, data_mode=active_mode)
            res = svc.list_stocks(
                query=args.query,
                sector=args.sector,
                rule_set=args.rule_set,
                passed_only=args.passed_only,
                min_market_cap_cr=args.min_market_cap,
                min_traded_value_cr=args.min_traded_value,
                limit=args.limit,
            )
            items = res.get("items", [])
            print(
                f"\nStocks (rule_set={res.get('rule_set')}, showing {len(items)}/{res.get('total', len(items))})"
            )
            head = (
                f"{'Symbol':<12}{'Name':<28}{'Sector':<24}{'MCap Cr':>10}"
                f"{'P/E':>7}{'ROE%':>7}{'Checks':>9}{'Score':>7}  Status"
            )
            print(head + "\n" + "-" * len(head))
            for item in items:
                mcap = "-" if item.get("market_cap_cr") is None else f"{item['market_cap_cr']:,.0f}"
                pe = "-" if item.get("pe") is None else f"{item['pe']:.1f}"
                raw_roe = item.get("roe")
                roe_pct = (raw_roe * 100.0 if abs(raw_roe) <= 2.5 else raw_roe) if raw_roe is not None else None
                roe = "-" if roe_pct is None else f"{roe_pct:.1f}"
                chk = f"{item.get('passed_count', 0)}/{item.get('evaluated_count', 0)}"
                sc = f"{item.get('composite_score', 0.0):.1f}"
                st = "PASS" if item.get("passed") else ("UNSYNCED" if item.get("source") == "unsynced_universe" else "FAIL")
                print(
                    f"{str(item.get('symbol', ''))[:11]:<12}"
                    f"{str(item.get('name', ''))[:27]:<28}"
                    f"{str(item.get('sector', ''))[:23]:<24}"
                    f"{mcap:>10}{pe:>7}{roe:>7}{chk:>9}{sc:>7}  {st}"
                )
    elif args.cmd == "analyze-stock":
        from .agents.stock_narrator import StockNarratorAgent
        from .data.stocks.sync import StockSyncService
        from .domain.stock_context import build_single_stock_analysis
        from .llm import flush_langfuse, trace_context
        from .llm.factory import get_llm

        active_mode = default_mode or s.data_mode
        sym = args.symbol.strip().upper().removesuffix(".NS")
        svc = StockSyncService(conn, data_mode=active_mode, snapshot_ttl_hours=s.stock_snapshot_ttl_hours)
        bundle = svc.ensure_stock_cached(sym, force_refresh=args.refresh)
        if bundle is None:
            print(f"Error: Stock '{sym}' not found in cache or provider.")
            return 2

        peers = svc.get_sector_peers(bundle.master.sector, exclude_symbol=bundle.master.symbol, rule_set=args.rule_set, limit=4)
        analysis = build_single_stock_analysis(
            master=bundle.master,
            snapshot=bundle.snapshot,
            financials=bundle.financials,
            prices=bundle.prices,
            sector_median=bundle.sector_median,
            sector_peers=peers,
            rule_set=args.rule_set,
            min_market_cap_cr=args.min_market_cap,
            min_traded_value_cr=args.min_traded_value,
        )
        if not args.no_narrative:
            try:
                client = get_llm()
            except Exception:
                client = None
            narrator = StockNarratorAgent(client)
            with trace_context(run_id=rid):
                analysis["narrative"] = narrator.narrate_single_stock(analysis)
            flush_langfuse()
        else:
            analysis["narrative"] = ""

        scr = analysis["active_screen"]
        val = analysis.get("valuation") or {}
        snap = analysis["snapshot"]
        print(
            f"\nRun {rid} | Analyze Stock: {bundle.master.name} ({bundle.master.symbol}) "
            f"[{bundle.master.sector}{', Bank/NBFC' if bundle.master.is_bank else ''}]"
        )
        print(
            f"Price: ₹{snap.get('price') or 0:,.2f} | Market Cap: ₹{snap.get('market_cap_cr') or 0:,.0f} Cr | "
            f"P/E: {snap.get('pe') if snap.get('pe') is not None else '-'} | "
            f"P/B: {snap.get('pb') if snap.get('pb') is not None else '-'} | "
            f"ROE: {snap.get('roe') if snap.get('roe') is not None else '-'}%"
        )
        print(
            f"Screen ({scr['rule_set'].upper()}): {'PASSED' if scr['passed'] else 'FAILED'} "
            f"({scr['passed_count']}/{scr['evaluated_count']} checks passed, score={scr['composite_score']:.1f}/100)"
        )
        if val.get("implied_fair_price") is not None:
            print(
                f"Relative Valuation: Implied Fair Price ₹{val['implied_fair_price']:,.2f} "
                f"({val.get('upside_pct', 0.0):+.1f}% | {val.get('verdict', 'N/A')})"
            )
        print("\nChecklist:")
        for c in scr.get("criteria", []):
            print(f"  [{c['status'].upper():<13}] {c['label']}: {c['actual_formatted']} (Target: {c['threshold_label']})")
        if analysis.get("narrative"):
            print(f"\nAnalyst Memo:\n  {analysis['narrative']}")
    elif args.cmd == "compare-stocks":
        from .agents.stock_narrator import StockNarratorAgent
        from .data.stocks.sync import StockSyncService
        from .domain.stock_compare import compare_stocks
        from .llm import flush_langfuse, trace_context
        from .llm.factory import get_llm

        active_mode = default_mode or s.data_mode
        syms = [x.strip().upper().removesuffix(".NS") for x in args.symbols]
        if len(syms) < 2 or len(syms) > 4:
            print("Error: Please provide between 2 and 4 stock symbols to compare.")
            return 2
        svc = StockSyncService(conn, data_mode=active_mode, snapshot_ttl_hours=s.stock_snapshot_ttl_hours)
        bundles = []
        for sym in syms:
            b = svc.ensure_stock_cached(sym)
            if b is None:
                print(f"Error: Stock '{sym}' not found.")
                return 2
            bundles.append(b)
        report = compare_stocks(
            bundles,
            rule_set=args.rule_set,
            min_market_cap_cr=args.min_market_cap,
            min_traded_value_cr=args.min_traded_value,
        )
        narrative = ""
        if not args.no_narrative:
            try:
                client = get_llm()
            except Exception:
                client = None
            narrator = StockNarratorAgent(client)
            with trace_context(run_id=rid):
                narrative = narrator.narrate_comparison(report)
            flush_langfuse()

        print(f"\nRun {rid} | Compare Stocks: {' vs '.join(syms)} (Rule Set: {args.rule_set.upper()})")
        for w in report.warnings:
            print(f"WARNING: {w}")
        print(
            f"Overall Winner: {report.overall_winner or 'Tie'} | "
            + " | ".join(f"{s}: {report.overall_scores.get(s, 0.0):.1f}/100" for s in syms)
        )
        col_headers = "".join(f"{s:>14}" for s in syms)
        head = f"{'Metric':<30}{col_headers}{'Winner':>12}"
        print("\n" + head + "\n" + "-" * len(head))
        for m in report.metrics:
            vals = "".join(f"{str(m.formatted_by_symbol.get(s, '-'))[:13]:>14}" for s in syms)
            print(f"{m.label[:29]:<30}{vals}{str(m.winner or '-'):>12}")
        if narrative:
            print(f"\nComparative Verdict:\n  {narrative}")
    elif args.cmd == "stock-rules":
        from .domain.stock_rulesets import compare_stock_rule_sets, get_stock_rules_catalog

        if args.compare or args.rule_set == "all":
            for rs_key in ("set1", "set2") if args.rule_set == "all" else (args.rule_set,):
                cat = get_stock_rules_catalog(rs_key)
                print(f"\n=== {cat['label']} ({cat['id']}) ===")
                print(f"Source: {cat['source']}")
                print(f"Summary: {cat['summary']}")
                for r in cat.get("non_bank_rules", []):
                    opt = " [OPTIONAL FILTER]" if r.get("optional") else ""
                    print(f"  - [{r['category']}] {r['label']}: {r['threshold']}{opt} ({r['source_ref']})")
            comp = compare_stock_rule_sets()
            print("\n=== Set 1 vs Set 2 Comparison ===")
            for d in comp.get("dimensions", []):
                print(f"  * {d['dimension']}: Set 1 = {d['set1']} | Set 2 = {d['set2']}")
        else:
            cat = get_stock_rules_catalog(args.rule_set)
            print(f"\n=== {cat['label']} ({cat['id']}) ===")
            print(f"Source: {cat['source']}")
            print(f"Summary: {cat['summary']}")
            print("\nNon-Bank Rules:")
            for r in cat.get("non_bank_rules", []):
                print(f"  - [{r['category']}] {r['label']}: {r['threshold']} ({r['source_ref']})")
            print("\nBank / NBFC Rules:")
            for r in cat.get("bank_rules", []):
                print(f"  - [{r['category']}] {r['label']}: {r['threshold']} ({r['source_ref']})")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
