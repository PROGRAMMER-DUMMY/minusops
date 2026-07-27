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


def _snapshot(conn):
    return {
        ("aws_s3_bucket", "acl"): ks.resolve(conn, "aws_s3_bucket", "acl"),
        ("aws_kms_key", None): ks.resolve(conn, "aws_kms_key"),
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
    before = ks.resolve(conn, "aws_kms_key")
    root = tmp_path / "claims"
    ks.export_jsonl(conn, str(root))
    conn.close()

    rebuilt = ks.init_db(str(tmp_path / "r.db"))
    ks.import_jsonl(rebuilt, str(root))
    assert ks.resolve(rebuilt, "aws_kms_key") == before
    rebuilt.close()
