"""
Coverage for core/providers/{base,aws}.py — flagged by the 2026-07-03 audit as having no
dedicated test file at all (only indirect coverage via test_credentials.py and
test_bcm_pricing_calculator.py's monkeypatching).

The Azure/GCP scaffolds and the one-implementation CloudProvider ABC were removed when
multi-cloud was dropped from scope, so the tests covering their "safe unknown defaults"
went with them. What remains is the contract that still exists: the factory, and AWS's
pricing methods actually delegating to the reviewed catalog.
"""
import pytest

import providers.base as pb
from providers.aws import AWSProvider


def test_get_provider_returns_aws():
    assert isinstance(pb.get_provider(), AWSProvider)
    assert isinstance(pb.get_provider("aws"), AWSProvider)
    assert pb.active_cloud() == "aws"


def test_get_provider_rejects_a_non_aws_cloud_instead_of_falling_back():
    # An unknown cloud must be an error, never a silent AWS fallback -- a caller asking for
    # azure and getting AWS credentials back would be the worst possible outcome.
    for cloud in ("oracle", "azure", "gcp"):
        with pytest.raises(ValueError, match=cloud):
            pb.get_provider(cloud)


def test_aws_provider_pricing_methods_delegate_to_pricing_catalog():
    provider = AWSProvider()
    assert provider.status == "implemented"
    entry = provider.resolve_resource_type("aws_glue_job")
    assert entry["service_code"] == "AWSGlue"
    free = provider.confirmed_free("aws_security_group")
    assert free["display_name"] == "Amazon VPC"
