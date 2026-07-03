"""
Coverage for core/providers/{base,aws,azure,gcp}.py — flagged by the 2026-07-03 audit as having
no dedicated test file at all (only indirect coverage via test_credentials.py and
test_bcm_pricing_calculator.py's monkeypatching). Focused on the contract itself: the safe
"unknown, not a crash" defaults on CloudProvider, and that AWS/Azure/GCP each honor them.
"""
import providers.base as pb
from providers.aws import AWSProvider
from providers.azure import AzureProvider
from providers.gcp import GCPProvider


def test_get_provider_factory_returns_the_right_class(monkeypatch):
    assert isinstance(pb.get_provider("aws"), AWSProvider)
    assert isinstance(pb.get_provider("azure"), AzureProvider)
    assert isinstance(pb.get_provider("gcp"), GCPProvider)


def test_get_provider_defaults_to_minus_cloud_env(monkeypatch):
    monkeypatch.setenv("MINUS_CLOUD", "azure")
    assert isinstance(pb.get_provider(), AzureProvider)


def test_get_provider_rejects_unknown_cloud():
    try:
        pb.get_provider("oracle")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "oracle" in str(exc)


def test_capabilities_matrix_reflects_status():
    matrix = pb.capabilities()
    assert matrix == {"aws": "implemented", "azure": "roadmap", "gcp": "roadmap"}


def test_base_pricing_methods_degrade_to_safe_defaults_not_exceptions():
    # Audit finding 2026-07-03: these used to raise NotImplementedError, which crashes an
    # uncautious caller instead of degrading gracefully as the docstring claimed.
    class BareProvider(pb.CloudProvider):
        name = "bare"

        def identity(self):
            return None, False

        def cost_by_service(self, months_back=6):
            return {"ok": False, "error": "n/a", "months": []}

        def anomalies(self, days_back=60):
            return None, "n/a"

        def owner(self, resource_hint):
            return None

    p = BareProvider()
    assert p.list_billable_services() == []
    assert p.resolve_resource_type("aws_glue_job") is None
    assert p.lookup_usage_dimensions("AWSGlue") == {}
    assert p.confirmed_free("aws_security_group") is None


def test_azure_and_gcp_inherit_safe_pricing_defaults():
    for provider in (AzureProvider(), GCPProvider()):
        assert provider.status == "roadmap"
        assert provider.list_billable_services() == []
        assert provider.resolve_resource_type("aws_glue_job") is None
        assert provider.confirmed_free("aws_security_group") is None
        assert provider.lookup_usage_dimensions("anything") == {}


def test_aws_provider_pricing_methods_delegate_to_pricing_catalog():
    provider = AWSProvider()
    assert provider.status == "implemented"
    entry = provider.resolve_resource_type("aws_glue_job")
    assert entry["service_code"] == "AWSGlue"
    free = provider.confirmed_free("aws_security_group")
    assert free["display_name"] == "Amazon VPC"


def test_azure_gcp_actuals_return_explicit_unsupported_not_silent_success():
    for provider in (AzureProvider(), GCPProvider()):
        account, connected = provider.identity()
        assert account is None and connected is False
        result = provider.cost_by_service()
        assert result["ok"] is False and result["months"] == []
        anomalies, error = provider.anomalies()
        assert anomalies is None and error
