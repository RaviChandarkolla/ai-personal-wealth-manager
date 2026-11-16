from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.agents.decision import determine_action
from wealth_advisor.audit.log import AuditLog
from wealth_advisor.config import Settings
from wealth_advisor.daily import (
    DailyScheduler,
    PositionStore,
    compute_conviction_deltas,
    confirm_rebalance,
    run_daily_cycle,
    simulate_daily_loop,
)
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.models import (
    Action,
    AnalystVerdict,
    Bucket,
    Citation,
    DailyOutcome,
    DossierEntry,
    Fact,
    Goal,
    Instrument,
    RiskPreference,
)
from wealth_advisor.notifications import CompositeNotifier, ConsoleNotifier, EmailNotifier
from wealth_advisor.pipeline import advise
from wealth_advisor.portfolio import (
    PortfolioStore,
    parse_portfolio_csv,
    valuate_portfolio,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def conn(demo_conn):
    dst = open_db(":memory:")
    demo_conn.backup(dst)
    return dst


def test_advise_persists_active_position(conn):
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    res = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)

    pos_store = PositionStore(conn)
    pos = pos_store.get(res.profile.profile_id, "default")
    assert pos is not None
    assert pos.run_id == res.run_id
    assert pos.established_date == AS_OF
    assert pos.last_checked_date == AS_OF
    assert pos.last_data_hash
    assert len(pos.rows) == len(res.decision.rows)


def test_section_1_5_deterministic_action_rules():
    cfg = Settings()
    # Unheld >= 70 -> BUY, < 70 -> None
    assert determine_action(
        75.0,
        is_held=False,
        hard_suitability_failed=False,
        drift_needs_increase=True,
        s=cfg,
    ) == Action.BUY
    assert determine_action(
        65.0,
        is_held=False,
        hard_suitability_failed=False,
        drift_needs_increase=True,
        s=cfg,
    ) is None

    # Held >= 70 and bucket underweight (drift_needs_increase=True) -> INCREASE
    assert determine_action(
        72.0,
        is_held=True,
        hard_suitability_failed=False,
        drift_needs_increase=True,
        s=cfg,
    ) == Action.INCREASE

    # Held >= 70 and no drift -> HOLD
    assert determine_action(
        72.0,
        is_held=True,
        hard_suitability_failed=False,
        drift_needs_increase=False,
        s=cfg,
    ) == Action.HOLD

    # Held in [50, 70) -> HOLD
    assert determine_action(
        60.0,
        is_held=True,
        hard_suitability_failed=False,
        drift_needs_increase=False,
        s=cfg,
    ) == Action.HOLD

    # Held in [35, 50) -> DECREASE
    assert determine_action(
        42.0,
        is_held=True,
        hard_suitability_failed=False,
        drift_needs_increase=False,
        s=cfg,
    ) == Action.DECREASE

    # Held < 35 -> EXIT
    assert determine_action(
        30.0,
        is_held=True,
        hard_suitability_failed=False,
        drift_needs_increase=False,
        s=cfg,
    ) == Action.EXIT

    # Held and fails hard suitability rule -> EXIT regardless of score
    assert determine_action(
        85.0,
        is_held=True,
        hard_suitability_failed=True,
        drift_needs_increase=True,
        s=cfg,
    ) == Action.EXIT


def test_unchanged_data_hits_cache_and_logs_hold_silently(conn):
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    res = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)

    llm = ScriptedLLM()
    console_out: list[str] = []
    notifier = ConsoleNotifier(printer=console_out.append)

    cycle = run_daily_cycle(
        conn,
        llm,
        profile_id=res.profile.profile_id,
        as_of=AS_OF,
        notifier=notifier,
    )
    assert cycle.outcome == DailyOutcome.HOLD
    assert cycle.cache_hit is True
    assert cycle.message == "checked, nothing changed"
    assert cycle.notification is None
    assert console_out == []
    assert llm.calls == 0  # Dossier/analyst skipped via data-hash cache

    entries = AuditLog(conn).entries(cycle.run_id)
    assert len(entries) == 1
    assert entries[0].step == "daily_cycle"
    assert entries[0].payload["outcome"] == "HOLD"
    assert entries[0].payload["message"] == "checked, nothing changed"


def test_30_day_replayed_nav_simulation_hold_rebalance_and_cooldown(conn):
    """M10 Done Criteria:
    Simulate 30 days of replayed NAV data — REBALANCE fires only on genuinely
    material changes; HOLD days are logged as 'checked, nothing changed'.
    """
    store = MetadataStore(conn)
    est_date = AS_OF - timedelta(days=30)

    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    initial = advise(goal, ScriptedLLM(), conn, as_of=est_date)
    profile_id = initial.profile.profile_id
    held_codes = [r.instrument_id for r in initial.decision.rows]
    target_fund_1 = held_codes[0]
    target_fund_2 = held_codes[1] if len(held_codes) > 1 else held_codes[0]

    # Seed initial portfolio matching the Day-0 recommendation so drift starts at ~0%
    csv_lines = ["code,units,avg_cost,as_of"]
    for row in initial.decision.rows:
        nav_0 = store.latest_nav_as_of(row.instrument_id, est_date)[1]
        units = row.amount / nav_0
        csv_lines.append(
            f"{row.instrument_id},{units:.6f},{nav_0:.4f},{(est_date - timedelta(days=400)).isoformat()}"
        )
    parsed = parse_portfolio_csv("\n".join(csv_lines), store, max_as_of=est_date)
    val_0 = valuate_portfolio(parsed.holdings, store, as_of=est_date)
    PortfolioStore(conn).save(profile_id, parsed.holdings, val_0)

    def hook_day_10(db_conn, current_date: date) -> None:
        # Material change #1 on Day 10: rating downgrade (5.0 -> 2.0) + manager change on held fund 1
        db_conn.execute(
            "UPDATE scheme SET rating = 2.0, manager = 'Interim Manager X', expense_ratio = 2.40 WHERE code = ?",
            (target_fund_1,),
        )
        db_conn.commit()

    def hook_day_13(db_conn, current_date: date) -> None:
        # Within 7-day cool-down window (3 days after Day 10): inject a non-hard portfolio drift (80% Equity / 20% Debt)
        # Should be suppressed by anti-overtrading cool-down!
        mstore = MetadataStore(db_conn)
        nav_eq = mstore.latest_nav_as_of("DEMO040", current_date)[1]
        nav_debt = mstore.latest_nav_as_of("DEMO010", current_date)[1]
        drift_csv = "\n".join(
            [
                "code,units,avg_cost,as_of",
                f"DEMO040,{80_000 / nav_eq:.6f},10.0,2024-01-10",
                f"DEMO010,{20_000 / nav_debt:.6f},10.0,2024-01-10",
            ]
        )
        p_drift = parse_portfolio_csv(drift_csv, mstore, max_as_of=current_date)
        v_drift = valuate_portfolio(p_drift.holdings, mstore, as_of=current_date)
        PortfolioStore(db_conn).save(profile_id, p_drift.holdings, v_drift)

    def hook_day_14(db_conn, current_date: date) -> None:
        # Restore baseline confirmed holdings after verifying Day 13 cool-down suppression
        mstore = MetadataStore(db_conn)
        curr_pos = PositionStore(db_conn).get(profile_id)
        assert curr_pos is not None
        val_restored = valuate_portfolio(curr_pos.holdings, mstore, as_of=current_date)
        PortfolioStore(db_conn).save(profile_id, curr_pos.holdings, val_restored)

    def hook_day_22(db_conn, current_date: date) -> None:
        # Material change #2 on Day 22 (after cool-down expires):
        # Restore target_fund_1 eligibility and downgrade target_fund_2 + manager change
        db_conn.execute(
            "UPDATE scheme SET rating = 5.0, manager = 'Manager Orchid', expense_ratio = 0.15 WHERE code = ?",
            (target_fund_1,),
        )
        db_conn.execute(
            "UPDATE scheme SET rating = 2.0, manager = 'New Manager Y' WHERE code = ?",
            (target_fund_2,),
        )
        db_conn.commit()

    console_out: list[str] = []
    console_notifier = ConsoleNotifier(printer=console_out.append)
    email_notifier = EmailNotifier()
    notifier = CompositeNotifier([console_notifier, email_notifier])

    sim_report = simulate_daily_loop(
        conn,
        ScriptedLLM(),
        profile_id=profile_id,
        days=30,
        start_date=est_date + timedelta(days=1),
        day_hooks={
            10: hook_day_10,
            13: hook_day_13,
            14: hook_day_14,
            22: hook_day_22,
        },
        notifier=notifier,
        auto_confirm=True,
    )

    assert sim_report.total_days == 30
    rebalance_days = [r for r in sim_report.days if r.outcome == DailyOutcome.REBALANCE]
    hold_days = [r for r in sim_report.days if r.outcome == DailyOutcome.HOLD]

    day_10_date = est_date + timedelta(days=10)
    day_13_date = est_date + timedelta(days=13)
    day_22_date = est_date + timedelta(days=22)

    # Material rebalances fire ONLY on Day 10 and Day 22; Day 13 was suppressed by cool-down
    assert [r.as_of for r in rebalance_days] == [day_10_date, day_22_date]
    assert sim_report.rebalance_count == 2
    assert sim_report.hold_count == 28
    assert sim_report.cooldown_suppressions >= 1

    # Day 13 specifically recorded cooldown_suppressed=True and outcome=HOLD
    res_day_13 = next(r for r in sim_report.days if r.as_of == day_13_date)
    assert res_day_13.outcome == DailyOutcome.HOLD
    assert res_day_13.cooldown_suppressed is True

    # Every HOLD day is logged in AuditLog with "checked, nothing changed"
    audit = AuditLog(conn)
    for h in hold_days:
        entries = audit.entries(h.run_id)
        hold_entry = next(e for e in entries if e.step == "daily_cycle")
        assert hold_entry.payload["message"] == "checked, nothing changed"

    # Notifications were sent ONLY on the 2 REBALANCE days
    assert len(email_notifier.sent) == 2
    assert len(email_notifier.emails) == 2
    assert len(console_out) == 2
    assert audit.verify() == (True, None)


def test_recommend_only_requires_user_confirmation(conn):
    est_date = AS_OF - timedelta(days=10)
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    initial = advise(goal, ScriptedLLM(), conn, as_of=est_date)
    profile_id = initial.profile.profile_id
    first_code = initial.decision.rows[0].instrument_id
    initial_run_id = initial.run_id

    # Trigger a hard event (rating downgrade + manager change)
    conn.execute(
        "UPDATE scheme SET rating = 1.0, manager = 'Replaced Manager' WHERE code = ?",
        (first_code,),
    )
    conn.commit()

    cycle = run_daily_cycle(
        conn,
        ScriptedLLM(),
        profile_id=profile_id,
        as_of=AS_OF,
        notifier=ConsoleNotifier(quiet=True),
        auto_confirm=False,
    )
    assert cycle.outcome == DailyOutcome.REBALANCE
    assert cycle.confirmed is False
    assert cycle.notification is not None
    assert cycle.notification.requires_confirmation is True

    # Active position run_id is NOT mutated until user confirms
    pos_before = PositionStore(conn).get(profile_id)
    assert pos_before is not None
    assert pos_before.run_id == initial_run_id
    assert pos_before.pending_rebalance is not None

    # Confirm rebalance -> active position updates to new recommendation
    pos_after = confirm_rebalance(conn, profile_id)
    assert pos_after.run_id == cycle.run_id
    assert pos_after.pending_rebalance is None
    assert pos_after.last_rebalance_date == AS_OF


def test_escalate_state_when_bucket_has_no_data(conn):
    est_date = AS_OF - timedelta(days=5)
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    initial = advise(goal, ScriptedLLM(), conn, as_of=est_date)
    profile_id = initial.profile.profile_id

    # Remove all Gold funds so MFResearchAgent returns NoData for the Gold bucket -> ESCALATE
    conn.execute("DELETE FROM scheme WHERE bucket = ?", (Bucket.GOLD.value,))
    conn.commit()

    cycle = run_daily_cycle(
        conn,
        ScriptedLLM(),
        profile_id=profile_id,
        as_of=AS_OF,
        force_refresh=True,
    )
    assert cycle.outcome == DailyOutcome.ESCALATE
    assert cycle.escalation_reasons

    escalations = PositionStore(conn).list_escalations(profile_id=profile_id)
    assert len(escalations) == 1
    assert escalations[0].profile_id == profile_id
    assert escalations[0].status == "PENDING_HUMAN_REVIEW"


def test_conviction_delta_detects_score_shift_and_hard_events(conn):
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    initial = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)
    pos = PositionStore(conn).get(initial.profile.profile_id)
    assert pos is not None

    target_id = pos.rows[0].instrument_id
    prev_entry = next(e for e in pos.entries if e.instrument.id == target_id)
    prev_verdict = pos.verdicts[target_id]

    # Mutate facts to simulate rating downgrade (5.0 -> 2.0) and manager change, plus -15pt score drop
    cit = Citation(source="amfi", id=f"{target_id}:test", as_of=AS_OF)
    new_facts = [
        f
        for f in prev_entry.facts
        if f.name not in ("rating", "manager")
    ] + [
        Fact(id=f"{target_id}:rating", name="rating", value=2.0, citation=cit),
        Fact(id=f"{target_id}:manager", name="manager", value="New Manager Z", citation=cit),
    ]
    new_entry = DossierEntry(instrument=prev_entry.instrument, facts=new_facts)
    curr_entries = [new_entry if e.instrument.id == target_id else e for e in pos.entries]
    curr_verdicts = dict(pos.verdicts)
    curr_verdicts[target_id] = AnalystVerdict(
        instrument_id=target_id,
        score=max(0.0, prev_verdict.score - 15.0),
        sub_scores=prev_verdict.sub_scores,
        confidence=prev_verdict.confidence,
        pros=prev_verdict.pros,
        cons=prev_verdict.cons,
    )

    deltas, hard_events = compute_conviction_deltas(
        pos=pos,
        curr_entries=curr_entries,
        curr_verdicts=curr_verdicts,
        held_codes=[r.instrument_id for r in pos.rows],
        s=Settings(),
    )
    d = next(item for item in deltas if item.instrument_id == target_id)
    assert d.score_delta == pytest.approx(-15.0, abs=0.1)
    assert d.breached is True
    assert d.hard_events
    assert any("rating downgraded" in ev for ev in hard_events)
    assert any("fund manager changed" in ev for ev in hard_events)


def test_daily_scheduler_0800_ist_calculation(conn):
    scheduler = DailyScheduler(conn, ScriptedLLM())
    before_8am = datetime(2026, 10, 9, 7, 30, tzinfo=IST)
    after_8am = datetime(2026, 10, 9, 9, 15, tzinfo=IST)

    assert scheduler.cron_expression == "0 8 * * *"
    assert scheduler.next_run_time(before_8am) == datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    assert scheduler.next_run_time(after_8am) == datetime(2026, 10, 10, 8, 0, tzinfo=IST)
