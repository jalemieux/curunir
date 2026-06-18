"""Engine tests: deterministic cash-flow arithmetic, depletion detection,
Monte-Carlo determinism + threshold flag, and stress-grid directionality."""
from src.financial_plan import engine


# --- deterministic projection -----------------------------------------------

def test_balance_rolls_forward_across_phase_boundary():
    """Accumulation grows the balance, then distribution draws it down — the
    end balance of one year is the start balance of the next."""
    out = engine.project({
        "current_age": 64, "retirement_age": 65, "end_age": 67,
        "current_balance": 100_000, "annual_contribution": 10_000,
        "annual_spending": 50_000, "real_return": 0.05, "volatility": 0.0,
    })
    rows = out["rows"]
    assert [r["age"] for r in rows] == [64, 65, 66]
    assert [r["phase"] for r in rows] == ["accumulation", "distribution", "distribution"]
    # age 64: (100000 + 10000) * 1.05 = 115500
    assert rows[0]["contribution"] == 10_000
    assert rows[0]["end_balance"] == 115_500.0
    # age 65: (115500 - 50000) * 1.05 = 68775
    assert rows[1]["start_balance"] == 115_500.0
    assert rows[1]["withdrawal"] == 50_000
    assert rows[1]["end_balance"] == 68_775.0
    # age 66: (68775 - 50000) * 1.05 = 19713.75
    assert rows[2]["end_balance"] == 19_713.75
    assert out["ending_balance"] == 19_713.75
    assert out["depleted"] is False
    assert out["depletion_age"] is None


def test_depletion_detected_and_flagged():
    out = engine.project({
        "current_age": 65, "retirement_age": 65, "end_age": 70,
        "current_balance": 100_000, "annual_contribution": 0,
        "annual_spending": 60_000, "real_return": 0.0,
    })
    assert out["depleted"] is True
    assert out["depletion_age"] == 66  # 100k -60k =40k; 40k -60k < 0
    assert out["ending_balance"] == 0.0


def test_project_requires_core_fields():
    import pytest
    with pytest.raises(ValueError):
        engine.project({"current_age": 40})  # missing balance/ages/spending


def test_project_rejects_inverted_ages():
    import pytest
    with pytest.raises(ValueError):
        engine.project({
            "current_age": 70, "retirement_age": 65, "end_age": 60,
            "current_balance": 100_000, "annual_spending": 1,
        })


# --- Monte Carlo ------------------------------------------------------------

_BASE = {
    "current_age": 50, "retirement_age": 65, "end_age": 90,
    "current_balance": 500_000, "annual_contribution": 20_000,
    "annual_spending": 60_000, "real_return": 0.05, "volatility": 0.12,
}


def test_monte_carlo_is_deterministic_for_a_fixed_seed():
    a = engine.monte_carlo(_BASE, n_sims=500, seed=42)
    b = engine.monte_carlo(_BASE, n_sims=500, seed=42)
    assert a["success_prob"] == b["success_prob"]
    assert a["ending_balance_p50"] == b["ending_balance_p50"]


def test_monte_carlo_seed_changes_result():
    a = engine.monte_carlo(_BASE, n_sims=500, seed=1)
    b = engine.monte_carlo(_BASE, n_sims=500, seed=2)
    # Different seeds give different paths (vanishingly unlikely to tie exactly).
    assert a["success_prob"] != b["success_prob"] or a["ending_balance_p50"] != b["ending_balance_p50"]


def test_threshold_met_true_for_an_overfunded_plan():
    out = engine.monte_carlo({
        "current_age": 60, "retirement_age": 65, "end_age": 90,
        "current_balance": 5_000_000, "annual_contribution": 0,
        "annual_spending": 30_000, "real_return": 0.05, "volatility": 0.10,
    }, n_sims=300, seed=7)
    assert out["success_prob"] == 1.0
    assert out["threshold_met"] is True
    assert out["n_sims"] == 300


def test_threshold_not_met_for_an_underfunded_plan():
    out = engine.monte_carlo({
        "current_age": 60, "retirement_age": 62, "end_age": 95,
        "current_balance": 100_000, "annual_contribution": 0,
        "annual_spending": 80_000, "real_return": 0.05, "volatility": 0.12,
    }, n_sims=300, seed=7)
    assert out["success_prob"] < 0.85
    assert out["threshold_met"] is False
    assert "p50" in out["depletion_age_percentiles"]


# --- stress grid ------------------------------------------------------------

def test_stress_grid_scenarios_shift_success_in_expected_direction():
    grid = engine.stress_grid(_BASE, n_sims=300, seed=11)
    by = {row["scenario"]: row for row in grid["scenarios"]}
    base = grid["base"]["success_prob"]
    # All four canned shocks plus a base reference.
    assert set(by) >= {"retire_2yr_early", "return_minus_20pct_y1",
                       "spending_plus_20pct", "ltc_late_life"}
    # More spending can never improve success.
    assert by["spending_plus_20pct"]["success_prob"] <= base
    # Retiring early (fewer earning years, more drawdown years) can't improve it.
    assert by["retire_2yr_early"]["success_prob"] <= base
    # Each scenario reports a success prob and a deterministic depletion age field.
    for row in grid["scenarios"]:
        assert 0.0 <= row["success_prob"] <= 1.0
        assert "depletion_age" in row


def test_render_markdown_contains_sections():
    md = engine.render_markdown(_BASE, n_sims=200, seed=3)
    assert "Probability of success" in md
    assert "Stress" in md
    assert "%" in md
