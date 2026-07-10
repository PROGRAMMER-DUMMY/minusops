"""
Discovery helper — turn a service/pattern into authoritative, citable sources, deterministically.

The architect path researches *current* services and schemas instead of relying on memory. This
module builds the exact documentation URLs (from the same predictable patterns catalogued in
docs/documentation_ledger.md) so research is structured and reproducible: every resource the
architect writes can be grounded in its real Terraform Registry schema, every service justified
against Well-Architected, and every price pulled from the live index. Records are cached so the
same lookup isn't re-fetched repeatedly.

This module constructs URLs and records; it does not fetch (the agent fetches via WebFetch). That
keeps it deterministic and unit-testable.
"""
import datetime
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(REPO_ROOT, "artifacts", "research")

REGISTRY = "https://registry.terraform.io/providers/hashicorp/aws/latest/docs"
AWSCLI = "https://awscli.amazonaws.com/v2/documentation/api/latest/reference"
WELL_ARCHITECTED = "https://developer.hashicorp.com/well-architected-framework"
AWS_WELL_ARCHITECTED = "https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html"
ARCHITECTURE_CENTER = "https://aws.amazon.com/architecture/"
SOLUTIONS_LIBRARY = "https://aws.amazon.com/solutions/"


def _strip_aws(t):
    return t[4:] if t.startswith("aws_") else t


def terraform_resource_url(resource_type):
    """Direct Registry page for an aws_* resource type (the schema the architect must ground in)."""
    return f"{REGISTRY}/resources/{_strip_aws(resource_type)}"


def terraform_datasource_url(data_source_type):
    return f"{REGISTRY}/data-sources/{_strip_aws(data_source_type)}"


def awscli_url(service, action):
    return f"{AWSCLI}/{service}/{action}.html"


def pricing_index_url(service_code):
    """Raw AWS price-list JSON index for a service code (supporting price discovery only — BCM
    remains the source of truth for published cost totals)."""
    return f"https://pricing.us-east-1.amazonaws.com/offers-v1.0/aws/{service_code}/current/index.json"


def sources_for(resource_types=(), data_sources=(), service_codes=()):
    """Assemble the authoritative-source set for a set of resources/services."""
    return {
        "terraform_resources": {rt: terraform_resource_url(rt) for rt in resource_types},
        "terraform_data_sources": {ds: terraform_datasource_url(ds) for ds in data_sources},
        "pricing_indexes": {sc: pricing_index_url(sc) for sc in service_codes},
        "well_architected": [WELL_ARCHITECTED, AWS_WELL_ARCHITECTED],
        "reference_architectures": [ARCHITECTURE_CENTER, SOLUTIONS_LIBRARY],
    }


def research_record(topic, resource_types=(), data_sources=(), service_codes=(), notes=""):
    """A structured, citable research record the architect builds before synthesizing."""
    return {
        "topic": topic,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "resolution_order": [
            "1. in-repo pattern (existing .tf / module)",
            "2. docs/information_library.md (authoritative portal)",
            "3. docs/documentation_ledger.md formula -> direct URL (below)",
            "4. general web search (only if the above miss)",
        ],
        "sources": sources_for(resource_types, data_sources, service_codes),
        "notes": notes,
    }


def _slug(topic):
    return re.sub(r"[^a-z0-9]+", "-", (topic or "record").lower()).strip("-") or "record"


def cache_path(topic):
    return os.path.join(CACHE_DIR, f"{_slug(topic)}.json")


def save_record(record):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = cache_path(record.get("topic", "record"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def load_record(topic):
    path = cache_path(topic)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Authoritative-source discovery for architecture synthesis")
    ap.add_argument("topic")
    ap.add_argument("--resource", action="append", default=[], help="aws_* resource type")
    ap.add_argument("--service-code", action="append", default=[], help="pricing serviceCode, e.g. AWSGlue")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args(argv)
    record = research_record(args.topic, resource_types=args.resource, service_codes=args.service_code)
    print(json.dumps(record, indent=2))
    if args.save:
        print(f"\ncached -> {save_record(record)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
