"""The reference-architecture corpus, and what it is allowed to say about our classifier.

`core/architecture/blueprint_data/` records which services 35 published data-pipeline
architectures name. The corpus is not an authority on how to build anything -- it is evidence
about what a data pipeline is usually made of, and its one job here is to make a gap in
`architecture_model` visible before someone finds it on a run.

A service that a third of the corpus names and `classify_role` drops into "other" lands in
the footer of every diagram we draw, and nothing anywhere says so. That is exactly how the
VPC gap survived: `_vpc` and `vpc_` both require a delimiter a bare `aws_vpc` does not have.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core", "architecture"))

import architecture_model  # noqa: E402

DATA = os.path.join(ROOT, "core", "architecture", "blueprint_data")


def _corpus():
    path = os.path.join(DATA, "blueprints.json")
    if not os.path.isfile(path):
        pytest.skip("no blueprint corpus; run core/architecture/blueprint_data/refresh.py")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _sources():
    with open(os.path.join(DATA, "sources.json"), encoding="utf-8") as handle:
        return json.load(handle)


def test_every_source_has_a_unique_key_and_an_https_url():
    sources = _sources()
    keys = [source["key"] for source in sources]

    assert len(keys) == len(set(keys)), "duplicate source key"
    for source in sources:
        assert source["url"].startswith("https://"), source["key"]
        assert source["provider"] and source["title"]


def test_the_corpus_is_wide_enough_to_argue_from():
    """One blog post is an opinion. A finding that thirty published architectures agree on is
    worth changing the classifier for."""
    corpus = _corpus()

    assert corpus["fetched_count"] >= 30, corpus["fetched_count"]
    assert len({b["provider"] for b in corpus["blueprints"]}) >= 2


def test_the_corpus_stores_no_page_text():
    """These pages belong to AWS, Databricks and others. What is kept is a count of which
    service names appear where, which is a fact about the corpus rather than a copy of it."""
    corpus = _corpus()

    for blueprint in corpus["blueprints"]:
        assert set(blueprint) == {"key", "provider", "title", "url", "characters",
                                  "services", "stages"}
        for value in blueprint["services"].values():
            assert isinstance(value, int)


def _service_frequency(corpus):
    frequency = {}
    for blueprint in corpus["blueprints"]:
        for service in blueprint["services"]:
            frequency[service] = frequency.get(service, 0) + 1
    return frequency


def test_no_service_a_tenth_of_the_corpus_names_is_classified_as_other():
    """"other" is the layer with no meaning: it lands in the footer beside IAM roles. A
    service this many published architectures name is part of a data pipeline, and failing to
    place it is a gap in `_RULES`, not a property of the resource."""
    corpus = _corpus()
    frequency = _service_frequency(corpus)
    threshold = max(2, len(corpus["blueprints"]) // 10)

    unplaced = sorted(
        (count, service) for service, count in frequency.items()
        if count >= threshold
        and architecture_model.layer_of(architecture_model.classify_role(service)) == "other")

    assert not unplaced, (
        "named by the corpus and placed nowhere: "
        + ", ".join(f"{service} ({count}/{len(corpus['blueprints'])})"
                    for count, service in reversed(unplaced)))


def test_a_bare_vpc_is_networking_and_so_is_everything_beside_it():
    """The needles were `_vpc` and `vpc_`; neither matches `aws_vpc`, and no rule mentioned a
    NAT gateway, an internet gateway, a route table or an elastic IP at all. Every resource
    the networking-vpc module creates classified as "other"."""
    for rtype in ("aws_vpc", "aws_subnet", "aws_nat_gateway", "aws_internet_gateway",
                  "aws_route_table", "aws_eip", "aws_vpc_endpoint"):
        assert architecture_model.classify_role(rtype) == "network", rtype


def test_networking_did_not_swallow_the_security_resources_beside_it():
    """A security group lives in a VPC and is not a networking resource. `network` follows
    `security` in the rules for exactly this reason."""
    for rtype in ("aws_security_group", "aws_vpc_security_group_rule",
                  "aws_network_acl"):
        assert architecture_model.classify_role(rtype) == "security", rtype


def test_every_role_the_rules_can_return_has_a_layer():
    """A role with no `ROLE_LAYER` entry silently becomes "other", which is the failure this
    module's own gap was made of."""
    roles = {rule[0] for rule in architecture_model._RULES} | {"stage", "store_other"}
    roles.discard("store")

    for role in roles:
        assert architecture_model.layer_of(role) != "other", role


def test_the_medallion_vocabulary_matches_what_the_corpus_uses():
    """raw/bronze, clean/silver, curated/gold are the words the published architectures use.
    A stage name we rank but nobody writes is a rank nothing will ever hit."""
    corpus = _corpus()
    seen = set()
    for blueprint in corpus["blueprints"]:
        seen.update(blueprint["stages"])

    assert {"raw", "curated"} <= seen
    assert seen & {"bronze", "silver", "gold"}
    for word in seen:
        assert architecture_model.stage_rank(word) != 40 or word in ("clean", "stage"), word
