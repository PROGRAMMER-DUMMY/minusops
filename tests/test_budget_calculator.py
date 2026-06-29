"""
budget_calculator must never fabricate a cost total — it only points to the BCM API.
"""
import json

import budget_calculator


def test_cost_guidance_never_reports_a_total():
    g = budget_calculator.cost_guidance()
    assert g["reportable"] is False
    assert g["bcm_pricing_calculator_required"] is True
    assert any("bcm_pricing_calculator.py" in c for c in g["commands"])
    # No numeric total should be present anywhere in the record.
    assert "monthly_grand_total" not in json.dumps(g)


def test_main_tolerates_legacy_sizing_flags(tmp_path, capsys):
    # Old callers/dispatcher may still pass sizing flags; main must not crash.
    code = budget_calculator.main(["--scale", "4", "--duration", "6", "--log-dir", str(tmp_path), "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reportable"] is False
    assert (tmp_path / "budget_estimation.json").exists()


def test_no_offline_pricing_table_remains():
    # The hardcoded OFFLINE_PRICING table and live-lookup must be gone.
    assert not hasattr(budget_calculator, "OFFLINE_PRICING_US_EAST_1")
    assert not hasattr(budget_calculator, "fetch_live_aws_price")
