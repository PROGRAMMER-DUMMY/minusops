"""
Unit economics and error-budget burn (PRD-FINOPS-2026-005, FR-17 and FR-19).

The doctrine constraint shapes every test here. `budget_calculator` exists to REFUSE to
invent a cost total, and its docstring says so: "anyone finishing this file by adding a rate
table or an arithmetic estimate has reintroduced the fabricated total it exists to refuse."

FR-17 is still implementable, because a ratio derived from an evidenced total is a different
thing from an invented total. The rule these tests enforce: divide a number AWS gave you,
never produce one. Without evidence, refuse.

Depends on: core/cost/budget_calculator.py, core/reporting/finops_agent.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import budget_calculator
import finops_agent


# --- FR-17: unit economics ------------------------------------------------------------

def test_unit_economics_refuses_without_an_evidenced_total():
    """The whole point. No BCM figure, no number -- and specifically no zero, which reads as
    'this is free' rather than 'this is unknown'."""
    result = budget_calculator.unit_economics(gb_processed=1000, runs=30)
    assert result["reportable"] is False
    assert result.get("cost_per_gb") is None
    assert result.get("cost_per_run") is None
    assert "commands" in result, "a refusal must say how to get the evidence"


def test_unit_economics_divides_an_evidenced_total():
    result = budget_calculator.unit_economics(
        total_usd=240.0, source="bcm-estimate.json", gb_processed=1200, runs=30)
    assert result["reportable"] is True
    assert result["cost_per_gb"] == 0.2
    assert result["cost_per_run"] == 8.0


def test_unit_economics_records_where_the_total_came_from():
    """A ratio with no provenance is indistinguishable from one that was made up."""
    result = budget_calculator.unit_economics(
        total_usd=100.0, source="bcm-actuals.json", gb_processed=500)
    assert result["source"] == "bcm-actuals.json"


def test_an_evidenced_total_without_a_source_is_still_refused():
    """Passing a bare float is exactly how a fabricated number would enter."""
    result = budget_calculator.unit_economics(total_usd=100.0, gb_processed=500)
    assert result["reportable"] is False


def test_zero_volume_yields_no_ratio_rather_than_infinity():
    """A pipeline that processed nothing has no cost per GB. inf, nan, or a crash are all
    worse than an honest absence."""
    result = budget_calculator.unit_economics(
        total_usd=100.0, source="bcm-estimate.json", gb_processed=0, runs=0)
    assert result["cost_per_gb"] is None
    assert result["cost_per_run"] is None
    assert result["reportable"] is True, "the total is still evidenced; only the ratios are absent"


def test_scale_curve_divides_each_measured_point_and_never_extrapolates():
    """BCM's scale_curve prices 1x, 5x and 10x usage separately because cloud cost is not
    linear -- tiered storage and committed-use discounts bend the curve. Multiplying the 1x
    figure would produce three numbers, two of them invented."""
    points = [
        {"factor": 1, "total_usd": 240.0, "gb_processed": 1200},
        {"factor": 5, "total_usd": 1000.0, "gb_processed": 6000},
        {"factor": 10, "total_usd": 1800.0, "gb_processed": 12000},
    ]
    curve = budget_calculator.unit_economics_curve(points, source="bcm-scale-curve.json")
    assert [round(p["cost_per_gb"], 4) for p in curve] == [0.2, 0.1667, 0.15]
    assert all(p["reportable"] for p in curve)


# --- FR-19: error budget burn ---------------------------------------------------------

def test_error_budget_minutes_matches_the_published_table():
    assert finops_agent.error_budget_minutes(99.0) == 432.0
    assert finops_agent.error_budget_minutes(99.5) == 216.0
    assert round(finops_agent.error_budget_minutes(99.9), 2) == 43.2
    assert round(finops_agent.error_budget_minutes(99.99), 2) == 4.32


def test_a_healthy_budget_leaves_teams_free_to_ship():
    burn = finops_agent.error_budget_burn(99.5, consumed_minutes=50.0)
    assert burn["state"] == "healthy"
    assert burn["remaining_minutes"] == 166.0
    assert round(burn["burned_pct"], 1) == 23.1


def test_a_mostly_spent_budget_is_not_reported_as_healthy():
    burn = finops_agent.error_budget_burn(99.5, consumed_minutes=200.0)
    assert burn["state"] == "at_risk"


def test_an_exhausted_budget_calls_for_a_feature_freeze():
    """Past zero the policy is not advice, it is a stop. Reporting it as 'at risk' would let
    the team keep shipping through the breach."""
    burn = finops_agent.error_budget_burn(99.5, consumed_minutes=300.0)
    assert burn["state"] == "feature_freeze"
    assert burn["remaining_minutes"] < 0


def test_failed_runs_convert_to_consumed_minutes():
    """A failed hourly run means the data was stale for that hour. Stating the model rather
    than hiding it: consumed = failed runs x the interval between runs."""
    assert finops_agent.consumed_minutes_from_runs(
        total_runs=24, failed_runs=3, run_interval_minutes=60) == 180.0


def test_burn_rate_alert_fires_on_a_fast_24h_burn():
    """The published policy: more than 10% of the 30-day budget burned in 24 hours means
    something upstream broke, regardless of how much budget is left overall."""
    fast = finops_agent.error_budget_burn(99.5, consumed_minutes=30.0, window_hours=24)
    assert fast["burn_alert"] is True
    slow = finops_agent.error_budget_burn(99.5, consumed_minutes=10.0, window_hours=24)
    assert slow["burn_alert"] is False


def test_an_slo_of_one_hundred_percent_is_refused():
    """A 100% SLO has a zero error budget, so every burn is infinite. That is a stated
    target nobody can meet, not a configuration."""
    try:
        finops_agent.error_budget_minutes(100.0)
    except ValueError:
        return
    raise AssertionError("a 100% SLO must be refused, not divided by")
