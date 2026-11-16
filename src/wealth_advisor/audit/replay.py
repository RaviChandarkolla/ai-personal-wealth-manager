"""`replay`: re-run the deterministic steps of a logged run and diff against stored outputs.

LLM steps are not re-executed; their stored outputs are the inputs to the later deterministic steps.
"""

import json

from pydantic import BaseModel

from ..config import AnalystWeights, RiskWeights, get_settings
from ..domain.allocation import allocation_band, target_allocation
from ..domain.portfolio_spec import custom_target
from ..domain.risk import RiskInputs, compute_risk_score
from ..domain.rulesets import use_rule_set
from ..domain.scoring import score_instrument
from ..domain.sizing import Candidate, size_positions, size_stock_sleeve
from ..models.core import PortfolioSpec
from ..models.enums import Bucket
from .log import AuditLog


class Check(BaseModel):
    name: str
    ok: bool
    expected: object = None
    actual: object = None


class ReplayReport(BaseModel):
    run_id: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def _norm(x: object) -> object:
    return json.loads(json.dumps(x, sort_keys=True, default=str))


def _check(name: str, expected: object, actual: object) -> Check:
    e, a = _norm(expected), _norm(actual)
    return Check(name=name, ok=e == a, expected=e, actual=a)


def replay(audit: AuditLog, run_id: str) -> ReplayReport:
    steps = {e.step: e.payload for e in audit.entries(run_id)}
    checks: list[Check] = []

    rule_set = None
    if p := steps.get("profile"):
        rule_set = p.get("fund_rule_set")
    if not rule_set and (d := steps.get("decision")):
        rule_set = d.get("fund_rule_set")

    with use_rule_set(rule_set):
        if p := steps.get("profile"):
            ri = RiskInputs(**p["risk_inputs"])
            risk = compute_risk_score(ri, RiskWeights(**p["risk_weights"]))
            band = allocation_band(risk.category, ri.horizon_years, ri.emergency_months)
            checks.append(_check("risk_score", p["risk"]["score"], risk.score))
            checks.append(_check("risk_category", p["risk"]["category"], risk.category.value))
            checks.append(_check("allocation_band", p["band"], band.model_dump(mode="json")))
            expected_target = {b.value: w for b, w in target_allocation(band).items()}
            if p.get("portfolio_spec"):
                spec = PortfolioSpec(**p["portfolio_spec"])
                expected_target = {b.value: w for b, w in custom_target(spec, get_settings()).items()}
            checks.append(_check("target_allocation", p["target"], expected_target))

        if a := steps.get("analysis"):
            w = AnalystWeights(**a["weights"])
            for v in a["verdicts"]:
                checks.append(_check(f"score:{v['instrument_id']}", v["score"],
                                     score_instrument(v["sub_scores"], w)))

        if d := steps.get("decision"):
            s = get_settings().model_copy(update=d["settings"])
            for bucket, info in d["sizing"].items():
                positions = size_positions(
                    [Candidate(**c) for c in info["candidates"]], info["weight"], d["total_amount"], s
                )
                checks.append(_check(f"sizing:{Bucket(bucket).value}", info["positions"],
                                     [p.model_dump() for p in positions]))
            if ss := d.get("stock_sizing"):
                if ss.get("candidates") and ss.get("sleeve_weight", 0) > 0:
                    stock_pos = size_stock_sleeve(
                        [Candidate(**c) for c in ss["candidates"]],
                        ss.get("sectors", {}),
                        ss["sleeve_weight"],
                        d["total_amount"],
                        s,
                        max_stocks=ss.get("max_stocks"),
                    )
                    checks.append(_check("sizing:stock_sleeve", ss["positions"],
                                         [p.model_dump() for p in stock_pos]))
        return ReplayReport(run_id=run_id, checks=checks)
