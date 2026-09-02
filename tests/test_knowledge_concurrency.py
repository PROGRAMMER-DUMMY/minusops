"""
Increment 2: two agent sessions can research concurrently without one getting
'database is locked'.

Deliberately NOT a thread-race test -- that would be flaky. These assert the two
settings that make concurrent access work, plus the one behaviour WAL actually buys:
a reader is not blocked by an in-flight writer.
"""


import knowledge_store

_TS = "2026-07-26T00:00:00Z"


def _insert(conn, text, commit=True):
    return knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text=text,
        method="structural", source_type="schema", provider="aws",
        valid_from=_TS, observed_at=_TS, commit=commit,
    )


def test_journal_mode_is_wal(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_busy_timeout_is_set_so_a_second_writer_waits_instead_of_failing(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    conn.close()


def test_reader_is_not_blocked_by_an_in_flight_writer(tmp_path):
    """The property WAL exists for. Under the default rollback journal this read raises
    'database is locked'; under WAL it returns the last committed state."""
    path = str(tmp_path / "c.db")
    writer = knowledge_store.init_db(path)
    _insert(writer, "committed before the open write")
    writer.commit()

    reader = knowledge_store.init_db(path)
    writer.execute("BEGIN IMMEDIATE")
    _insert(writer, "uncommitted", commit=False)
    try:
        rows = reader.execute("SELECT COUNT(*) AS n FROM claims").fetchone()
        assert rows["n"] == 1, "reader must see committed state, not the in-flight write"
    finally:
        writer.rollback()
        writer.close()
        reader.close()
