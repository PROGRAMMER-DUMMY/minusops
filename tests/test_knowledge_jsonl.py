"""
Increment 3: claims live in git-committed JSONL; claims.db is a rebuildable cache.

The load-bearing property is round-trip identity -- deleting the cache and rebuilding
from JSONL must not change a single resolve() answer, including invalidation chains and
adjudications, which resolve() reads.
"""
import json

import knowledge_store as ks
import knowledge_delegation as kd

_T1 = "2026-07-20T00:00:00Z"
_T2 = "2026-07-26T00:00:00Z"


def _seed(conn):
    """A store exercising every shape resolve() cares about."""
    a = ks.insert_claim(conn, resource_type="aws_s3_bucket", attribute="acl",
                        claim_text="acl deprecated", method="structural", source_type="schema",
                        provider="aws", valid_from=_T1, observed_at=_T1)
    b = ks.insert_claim(conn, resource_type="aws_s3_bucket", attribute="acl",
                        claim_text="acl removed", method="structural", source_type="schema",
                        provider="aws", valid_from=_T2, observed_at=_T2)
    ks.invalidate_claim(conn, a, valid_until=_T2, invalidated_by=b)
    ks.insert_claim(conn, resource_type="aws_kms_key", attribute=None,
                    claim_text="rotation recommended", method="semantic",
                    source_type="agent_delegated", provider="aws",
                    valid_from=_T1, observed_at=_T1)
    ks.insert_claim(conn, scope="architecture", resource_type=None, attribute=None,
                    claim_text="sub-second -> Kinesis not batch Glue", method="semantic",
                    source_type="agent_delegated", provider="aws",
                    valid_from=_T1, observed_at=_T1)
    return a, b


def _semantic(result):
    """resolve() minus the machine-local row ids.

    Ids are deliberately NOT stable across a rebuild: they are per-machine autoincrements,
    which is exactly why cross-references travel as content_hash. The property that must
    survive a round trip is the ANSWER -- status, reason, and which claim won -- not the
    integer SQLite happened to assign locally.
    """
    def strip(claim):
        return {k: v for k, v in (claim or {}).items() if k not in ("id", "invalidated_by")}
    return {
        "status": result["status"],
        "reason": result["reason"],
        "winner": strip(result.get("winner")),
        "claims": sorted((strip(c) for c in result.get("claims", [])),
                         key=lambda c: c.get("content_hash") or ""),
    }


def _snapshot(conn):
    return {
        ("aws_s3_bucket", "acl"): _semantic(ks.resolve(conn, "aws_s3_bucket", "acl")),
        ("aws_kms_key", None): _semantic(ks.resolve(conn, "aws_kms_key")),
    }


def test_export_shards_by_resource_type_and_scope(tmp_path):
    conn = ks.init_db(str(tmp_path / "c.db"))
    _seed(conn)
    root = tmp_path / "claims"
    ks.export_jsonl(conn, str(root))
    names = {p.name for p in root.glob("*.jsonl")}
    assert "aws_s3_bucket.jsonl" in names
    assert "aws_kms_key.jsonl" in names
    assert "_architecture.jsonl" in names, "cross-cutting claims shard by scope, not type"
    lines = (root / "aws_s3_bucket.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(ln)["resource_type"] == "aws_s3_bucket" for ln in lines)
    conn.close()


def test_rebuilding_the_cache_from_jsonl_preserves_every_resolve_answer(tmp_path):
    conn = ks.init_db(str(tmp_path / "c.db"))
    _seed(conn)
    before = _snapshot(conn)
    root = tmp_path / "claims"
    ks.export_jsonl(conn, str(root))
    conn.close()

    rebuilt = ks.init_db(str(tmp_path / "rebuilt.db"))
    ks.import_jsonl(rebuilt, str(root))
    assert _snapshot(rebuilt) == before
    rebuilt.close()


def test_import_is_idempotent(tmp_path):
    """Rebuilding twice must not duplicate claims -- the cache is derived, not appended."""
    conn = ks.init_db(str(tmp_path / "c.db"))
    _seed(conn)
    root = tmp_path / "claims"
    ks.export_jsonl(conn, str(root))
    conn.close()

    rebuilt = ks.init_db(str(tmp_path / "r.db"))
    ks.import_jsonl(rebuilt, str(root))
    n1 = rebuilt.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    ks.import_jsonl(rebuilt, str(root))
    n2 = rebuilt.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    assert n1 == n2 == 4
    rebuilt.close()


def test_adjudications_survive_the_round_trip(tmp_path):
    """resolve() reads claim_adjudications, so a round trip that drops them silently
    changes verdicts."""
    conn = ks.init_db(str(tmp_path / "c.db"))
    a, b = _seed(conn)
    active = [r["id"] for r in ks._active_claims(conn, "aws_kms_key", None)]
    kd.record_delegation_verdict(
        conn, "aws_kms_key", None, claim_text="rotation confirmed required",
        valid_from=_T2, provider="aws", adjudicated_ids=active)
    before = _semantic(ks.resolve(conn, "aws_kms_key"))
    root = tmp_path / "claims"
    ks.export_jsonl(conn, str(root))
    conn.close()

    rebuilt = ks.init_db(str(tmp_path / "r.db"))
    ks.import_jsonl(rebuilt, str(root))
    assert _semantic(ks.resolve(rebuilt, "aws_kms_key")) == before
    rebuilt.close()


def test_two_branches_that_both_allocated_id_1_merge_without_collision(tmp_path):
    """P1.1: the marked ponytail shortcut. Ids are machine-local -- two people researching
    on separate branches both get id 1, and a merged corpus must not silently drop one or
    mis-wire an invalidation chain to the wrong claim."""
    a = ks.init_db(str(tmp_path / "a.db"))
    ks.insert_claim(a, resource_type="aws_s3_bucket", attribute="acl",
                    claim_text="alice's finding", method="semantic",
                    source_type="agent_researched", provider="aws",
                    valid_from=_T1, observed_at=_T1)
    root = tmp_path / "claims"
    ks.export_jsonl(a, str(root))
    a.close()

    b = ks.init_db(str(tmp_path / "b.db"))
    ks.insert_claim(b, resource_type="aws_kms_key", attribute="rotation",
                    claim_text="bob's finding", method="semantic",
                    source_type="agent_researched", provider="aws",
                    valid_from=_T1, observed_at=_T1)
    ks.export_jsonl(b, str(root))          # merged corpus, both were id 1
    b.close()

    merged = ks.init_db(str(tmp_path / "merged.db"))
    ks.import_jsonl(merged, str(root))
    texts = {r["claim_text"] for r in merged.execute("SELECT claim_text FROM claims")}
    assert texts == {"alice's finding", "bob's finding"}, "a claim was lost to an id collision"
    merged.close()


def test_an_invalidation_chain_survives_an_id_collision(tmp_path):
    """The dangerous half: if invalidated_by is resolved by raw id, a merge can point a
    supersession at an unrelated claim -- silently wrong, not loudly broken."""
    a = ks.init_db(str(tmp_path / "a.db"))
    old, new = _seed(a)
    root = tmp_path / "claims"
    ks.export_jsonl(a, str(root))
    before = ks.resolve(a, "aws_s3_bucket", "acl")["winner"]["claim_text"]
    a.close()

    b = ks.init_db(str(tmp_path / "b.db"))
    ks.insert_claim(b, resource_type="aws_athena_workgroup", attribute="state",
                    claim_text="unrelated claim that also got a low id", method="semantic",
                    source_type="agent_researched", provider="aws",
                    valid_from=_T1, observed_at=_T1)
    ks.export_jsonl(b, str(root))
    b.close()

    merged = ks.init_db(str(tmp_path / "m.db"))
    ks.import_jsonl(merged, str(root))
    assert ks.resolve(merged, "aws_s3_bucket", "acl")["winner"]["claim_text"] == before
    merged.close()
