import sqlite3
from datetime import date

from fakes import Stock360FakeLLM
from wealth_advisor.agents.stock_360_analyst import Stock360Analyst, completeness
from wealth_advisor.data.stocks.report_data import ReportDataStore, ensure_report_data
from wealth_advisor.data.stocks.synthetic import SyntheticStockProvider
from wealth_advisor.domain.stock360.assemble import ReportContext
from wealth_advisor.domain.stock360.mechanical import ACTIONS
from wealth_advisor.domain.stock360.template import SECTIONS


def _ctx(sym="TCS"):
    p = SyntheticStockProvider()
    st = ReportDataStore(sqlite3.connect(":memory:"))
    ensure_report_data(st, p, sym)
    return ReportContext(symbol=sym, snapshot=p.get_snapshot(sym), annual=p.get_financials(sym),
                         quarterly=st.get_quarterly(sym), bars=st.get_daily(sym), index=st.get_index("^NSEI"),
                         actions=st.get_actions(sym), today=date(2025, 6, 1))


def test_full_report_four_calls_and_complete():
    llm = Stock360FakeLLM()
    r = Stock360Analyst(llm, max_llm_calls=8).build(_ctx())
    assert llm.calls == 4 and r.llm_calls == 4
    assert [s.id for s in r.sections] == [s.id for s in SECTIONS]
    assert completeness(r) == []
    assert all(s.commentary for s in r.sections)
    assert r.final and r.final.action in ACTIONS and r.final.stars == r.mechanical.stars
    assert any("educational" in d.lower() or "advice" in d.lower() for d in r.disclosures)


def test_ungrounded_number_triggers_one_retry_then_passes():
    llm = Stock360FakeLLM(bad_first=True)
    r = Stock360Analyst(llm).build(_ctx())
    assert llm.calls == 5  # part 1 retried once
    assert "98765" not in " ".join(s.commentary for s in r.sections)
    assert "previous answer failed validation" in llm.prompts[1]


def test_persistent_ungrounded_text_is_replaced_deterministically():
    llm = Stock360FakeLLM(always_bad=True)
    r = Stock360Analyst(llm, max_llm_calls=8).build(_ctx())
    assert "98765" not in " ".join(s.commentary for s in r.sections)
    assert any("ungrounded" in w for w in r.warnings)
    assert all(s.commentary for s in r.sections)


def test_quotes_are_labelled_simulated():
    r = Stock360Analyst(Stock360FakeLLM(quote=True)).build(_ctx())
    assert all("simulated" in s.commentary.lower() for s in r.sections)


def test_mechanical_only_when_no_llm_or_llm_fails():
    for llm in (None, Stock360FakeLLM(fail=True)):
        r = Stock360Analyst(llm).build(_ctx())
        assert r.final and r.final.action == r.mechanical.action
        assert "unavailable" in r.sections[1].commentary.lower()
        assert len(r.sections) == 18


def test_budget_exhaustion_falls_back():
    llm = Stock360FakeLLM()
    r = Stock360Analyst(llm, max_llm_calls=2).build(_ctx())
    assert llm.calls == 2 and any("budget" in w for w in r.warnings)
    assert r.final


def test_llm_action_is_clamped_to_one_band():
    r = Stock360Analyst(Stock360FakeLLM(action="STRONG BUY")).build(_ctx())
    i, m = ACTIONS.index(r.final.action), ACTIONS.index(r.mechanical.action)
    assert abs(i - m) <= 1
