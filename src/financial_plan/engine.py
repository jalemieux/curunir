"""Retirement / goal projection engine — pure, deterministic, stateless.

Mirrors the balance-sheet engine's contract (synchronous, importable for tests
and the eval anchor) but holds **no store**: projection is computation, not
persistence. Every function takes plan inputs as a dict and returns structured
results. Starting balances flow in from the *caller* (e.g. the CLI's
`--from-portfolio`, which reads `src.portfolio.engine.networth`) so the two
engines stay decoupled but composable.

The math lives here — never in LLM prose — so a fixed seed + inputs yields a
reproducible probability the eval harness can anchor against.

## Conservative-by-default assumptions

Returns are **real** (inflation-adjusted), not nominal: a plan denominated in
today's dollars with a real return is honest about purchasing power. The
defaults below are deliberately conservative — under-promising is the safe
error for a retirement plan, where overestimating return is how people run out
of money. The caller can override any of them per call, but the *defaults*
should never flatter a plan.
"""
from __future__ import annotations

import random
import statistics

# Real (inflation-adjusted) annual return for a balanced/equity-tilted
# portfolio. Long-run real equity returns have run ~6.5-7%; we haircut to 5%
# so the base case doesn't lean on a rosy historical window. DON'T raise this
# to make a plan "work" — that just hides the risk.
DEFAULT_REAL_RETURN = 0.05
# Annual volatility (std-dev of real returns). Sets the width of the Monte-Carlo
# return distribution; ~12% is a conservative, slightly-elevated equity figure
# so the tails (sequence-of-returns risk) aren't understated.
DEFAULT_VOLATILITY = 0.12
# A plan must clear this Monte-Carlo probability of success to be considered
# viable. 85% is the persona's bar: a plan that succeeds in fewer than 85% of
# simulated futures is not a plan you retire on.
SUCCESS_THRESHOLD = 0.85

# LTC (long-term-care) stress: a single large one-time withdrawal late in life.
DEFAULT_LTC_AGE = 80
DEFAULT_LTC_AMOUNT = 100_000

_REQUIRED = ("current_age", "retirement_age", "end_age",
             "current_balance", "annual_spending")


def _normalize(inputs: dict) -> dict:
    """Validate and fill defaults. Raises ValueError on missing/incoherent
    inputs so the tool/CLI can surface a clean {error,hint}."""
    missing = [k for k in _REQUIRED if inputs.get(k) is None]
    if missing:
        raise ValueError(f"missing required input(s): {', '.join(missing)}")

    p = {
        "current_age": int(inputs["current_age"]),
        "retirement_age": int(inputs["retirement_age"]),
        "end_age": int(inputs["end_age"]),
        "current_balance": float(inputs["current_balance"]),
        "annual_contribution": float(inputs.get("annual_contribution") or 0.0),
        "annual_spending": float(inputs["annual_spending"]),
        "real_return": float(inputs.get("real_return", DEFAULT_REAL_RETURN)),
        "volatility": float(inputs.get("volatility", DEFAULT_VOLATILITY)),
        # Optional stress levers (used by stress_grid; harmless when unset).
        "first_year_return": (None if inputs.get("first_year_return") is None
                              else float(inputs["first_year_return"])),
        "ltc_age": (None if inputs.get("ltc_age") is None
                    else int(inputs["ltc_age"])),
        "ltc_amount": float(inputs.get("ltc_amount") or 0.0),
    }
    if not (p["current_age"] <= p["retirement_age"] <= p["end_age"]):
        raise ValueError("ages must satisfy current_age <= retirement_age <= end_age")
    if p["current_age"] < 0 or p["current_balance"] < 0:
        raise ValueError("current_age and current_balance must be non-negative")
    if p["annual_spending"] < 0 or p["annual_contribution"] < 0:
        raise ValueError("spending and contribution must be non-negative")
    if p["volatility"] < 0:
        raise ValueError("volatility must be non-negative")
    return p


def _step(start: float, age: int, p: dict, ret: float) -> tuple:
    """Advance one year. Contributions/withdrawals apply at the start of the
    year; growth then compounds the remaining balance. Returns
    (contribution, withdrawal, growth, end_balance)."""
    contribution = withdrawal = 0.0
    if age < p["retirement_age"]:
        contribution = p["annual_contribution"]
        base = start + contribution
    else:
        withdrawal = p["annual_spending"]
        base = start - withdrawal
    if p["ltc_amount"] and p["ltc_age"] is not None and age == p["ltc_age"]:
        withdrawal += p["ltc_amount"]
        base -= p["ltc_amount"]
    if base <= 0:
        return contribution, withdrawal, 0.0, 0.0
    growth = base * ret
    return contribution, withdrawal, growth, base + growth


def project(inputs: dict) -> dict:
    """Deterministic year-by-year cash flow from current_age to end_age.

    Returns {rows, depleted, depletion_age, ending_balance}. Each row is
    {year, age, phase, start_balance, contribution, withdrawal, growth,
    end_balance}. `real_return` is applied every year (volatility is ignored
    here — that's what monte_carlo is for); a one-time `first_year_return`
    override models a year-1 shock."""
    p = _normalize(inputs)
    rows: list[dict] = []
    bal = p["current_balance"]
    depleted = False
    depletion_age: int | None = None
    for i, age in enumerate(range(p["current_age"], p["end_age"])):
        ret = p["real_return"]
        if i == 0 and p["first_year_return"] is not None:
            ret = p["first_year_return"]
        contribution, withdrawal, growth, end = _step(bal, age, p, ret)
        if end <= 0 and not depleted and age >= p["retirement_age"]:
            depleted = True
            depletion_age = age
        rows.append({
            "year": i + 1,
            "age": age,
            "phase": "accumulation" if age < p["retirement_age"] else "distribution",
            "start_balance": round(bal, 2),
            "contribution": round(contribution, 2),
            "withdrawal": round(withdrawal, 2),
            "growth": round(growth, 2),
            "end_balance": round(end, 2),
        })
        bal = end
    return {
        "rows": rows,
        "depleted": depleted,
        "depletion_age": depletion_age,
        "ending_balance": round(bal, 2),
    }


def _simulate_path(p: dict, rng: random.Random) -> tuple:
    """One stochastic path. Returns (survived, ending_balance, depletion_age)."""
    bal = p["current_balance"]
    depletion_age: int | None = None
    for i, age in enumerate(range(p["current_age"], p["end_age"])):
        if i == 0 and p["first_year_return"] is not None:
            ret = p["first_year_return"]
        else:
            ret = rng.gauss(p["real_return"], p["volatility"])
        _, _, _, bal = _step(bal, age, p, ret)
        if bal <= 0 and depletion_age is None and age >= p["retirement_age"]:
            depletion_age = age
            bal = 0.0
            break
    survived = depletion_age is None
    return survived, round(bal, 2), depletion_age


def _percentiles(values: list[float], pcts=(10, 50, 90)) -> dict:
    """Inclusive linear-interpolation percentiles (statistics.quantiles is
    n-cut only, so compute directly for arbitrary points). Empty -> {}."""
    if not values:
        return {}
    s = sorted(values)
    out = {}
    for pct in pcts:
        if len(s) == 1:
            out[f"p{pct}"] = round(s[0], 2)
            continue
        rank = (pct / 100) * (len(s) - 1)
        lo = int(rank)
        frac = rank - lo
        hi = min(lo + 1, len(s) - 1)
        out[f"p{pct}"] = round(s[lo] + (s[hi] - s[lo]) * frac, 2)
    return out


def monte_carlo(inputs: dict, n_sims: int = 1000, seed: int = 0) -> dict:
    """Run `n_sims` seeded paths drawing each year's real return from
    Normal(real_return, volatility). Returns success probability against the
    0.85 threshold plus ending-balance and depletion-age percentiles.

    Seeded: a fixed (inputs, n_sims, seed) yields a reproducible result."""
    p = _normalize(inputs)
    rng = random.Random(seed)
    successes = 0
    ending_balances: list[float] = []
    depletion_ages: list[int] = []
    for _ in range(n_sims):
        survived, ending, dep_age = _simulate_path(p, rng)
        if survived:
            successes += 1
        ending_balances.append(ending)
        if dep_age is not None:
            depletion_ages.append(dep_age)
    success_prob = round(successes / n_sims, 4) if n_sims else 0.0
    end_pct = _percentiles(ending_balances)
    return {
        "success_prob": success_prob,
        "threshold": SUCCESS_THRESHOLD,
        "threshold_met": success_prob >= SUCCESS_THRESHOLD,
        "n_sims": n_sims,
        "ending_balance_p10": end_pct.get("p10"),
        "ending_balance_p50": end_pct.get("p50"),
        "ending_balance_p90": end_pct.get("p90"),
        "depletion_age_percentiles": _percentiles([float(a) for a in depletion_ages]),
    }


# Canned stress scenarios. Each maps base inputs -> shocked inputs. A plan the
# persona will stand behind must clear the threshold under *these*, not just the
# base case.
def _shock_retire_early(inp: dict) -> dict:
    return {**inp, "retirement_age": int(inp["retirement_age"]) - 2}


def _shock_bad_first_year(inp: dict) -> dict:
    return {**inp, "first_year_return": -0.20}


def _shock_higher_spending(inp: dict) -> dict:
    return {**inp, "annual_spending": float(inp["annual_spending"]) * 1.20}


def _shock_ltc(inp: dict) -> dict:
    return {**inp, "ltc_age": inp.get("ltc_age") or DEFAULT_LTC_AGE,
            "ltc_amount": inp.get("ltc_amount") or DEFAULT_LTC_AMOUNT}


_STRESS = {
    "retire_2yr_early": _shock_retire_early,
    "return_minus_20pct_y1": _shock_bad_first_year,
    "spending_plus_20pct": _shock_higher_spending,
    "ltc_late_life": _shock_ltc,
}


def stress_grid(inputs: dict, n_sims: int = 1000, seed: int = 0) -> dict:
    """Re-run project + monte_carlo for the base case and each canned shock.

    Returns {base, scenarios:[{scenario, success_prob, threshold_met,
    depletion_age}]}. `depletion_age` is the deterministic projection's
    depletion age under that shock (None if the base path survives)."""
    _normalize(inputs)  # validate once up front

    def _row(label: str, shocked: dict) -> dict:
        mc = monte_carlo(shocked, n_sims=n_sims, seed=seed)
        proj = project(shocked)
        return {
            "scenario": label,
            "success_prob": mc["success_prob"],
            "threshold_met": mc["threshold_met"],
            "depletion_age": proj["depletion_age"],
        }

    base = _row("base", dict(inputs))
    scenarios = [_row(label, shock(inputs)) for label, shock in _STRESS.items()]
    return {"base": base, "scenarios": scenarios}


def render_markdown(inputs: dict, n_sims: int = 1000, seed: int = 0) -> str:
    """Human-readable plan: assumptions, the cash-flow table, the Monte-Carlo
    success probability against the 85% bar, and the stress grid."""
    p = _normalize(inputs)
    proj = project(inputs)
    mc = monte_carlo(inputs, n_sims=n_sims, seed=seed)
    grid = stress_grid(inputs, n_sims=n_sims, seed=seed)

    verdict = "PASSES" if mc["threshold_met"] else "FAILS"
    lines = [
        "# Financial Plan Projection",
        "",
        "## Assumptions (real / inflation-adjusted)",
        "",
        f"- Current age {p['current_age']} → retire at {p['retirement_age']} "
        f"→ horizon age {p['end_age']}",
        f"- Starting balance: {p['current_balance']:,.0f}",
        f"- Annual contribution (accumulation): {p['annual_contribution']:,.0f}",
        f"- Annual spending (distribution): {p['annual_spending']:,.0f}",
        f"- Real return: {p['real_return'] * 100:.1f}%  |  "
        f"volatility: {p['volatility'] * 100:.1f}%",
        "",
        f"## Monte Carlo ({mc['n_sims']:,} sims, seed {seed})",
        "",
        f"- **Probability of success: {mc['success_prob'] * 100:.1f}%** "
        f"(bar: {SUCCESS_THRESHOLD * 100:.0f}% → **{verdict}**)",
        f"- Ending balance p10 / p50 / p90: "
        f"{(mc['ending_balance_p10'] or 0):,.0f} / "
        f"{(mc['ending_balance_p50'] or 0):,.0f} / "
        f"{(mc['ending_balance_p90'] or 0):,.0f}",
        "",
        "## Stress test (must also clear the bar)",
        "",
        "| scenario | success | clears 85% | depletion age |",
        "|---|---:|:---:|---:|",
        f"| base | {grid['base']['success_prob'] * 100:.1f}% | "
        f"{'yes' if grid['base']['threshold_met'] else 'NO'} | "
        f"{grid['base']['depletion_age'] or '—'} |",
    ]
    for row in grid["scenarios"]:
        lines.append(
            f"| {row['scenario']} | {row['success_prob'] * 100:.1f}% | "
            f"{'yes' if row['threshold_met'] else 'NO'} | "
            f"{row['depletion_age'] or '—'} |")
    lines += [
        "",
        "## Cash flow (deterministic, real dollars)",
        "",
        "| age | phase | start | +contrib | −spend | growth | end |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in proj["rows"]:
        lines.append(
            f"| {r['age']} | {r['phase']} | {r['start_balance']:,.0f} | "
            f"{r['contribution']:,.0f} | {r['withdrawal']:,.0f} | "
            f"{r['growth']:,.0f} | {r['end_balance']:,.0f} |")
    if proj["depleted"]:
        lines += ["", f"> ⚠️ Portfolio depletes at age {proj['depletion_age']} "
                      "in the deterministic (base-return) path."]
    return "\n".join(lines) + "\n"
