"""
Issue #2 / decision #15 -- regeneration must not silently destroy data.

Terraform identity IS the resource address. If regeneration places the same logical bucket
at a new address, Terraform reads destroy + create -- and on S3/RDS that is data loss, shown
in the report as an ordinary delete-then-create with no hint the two are the same thing.

Terraform's own answer is a `moved` block: declare from/to and the resource is re-keyed in
state with no destroy. So this detects rename-SHAPED churn and requires the moved block,
rather than inventing a parallel mechanism.
"""
import address_churn

_BUCKET_ID = {"bucket": "acme-prod-lake"}


def _plan(destroy_addr, create_addr, rtype="aws_s3_bucket", before=None, after=None):
    return {"resource_changes": [
        {"address": destroy_addr, "mode": "managed", "type": rtype,
         "change": {"actions": ["delete"], "before": before or _BUCKET_ID, "after": None}},
        {"address": create_addr, "mode": "managed", "type": rtype,
         "change": {"actions": ["create"], "before": None, "after": after or _BUCKET_ID}},
    ]}


def test_a_stateful_rename_without_a_moved_block_is_flagged():
    result = address_churn.classify(
        _plan("aws_s3_bucket.data", "module.storage.aws_s3_bucket.lake"), moved_blocks=[])
    assert result["blocked"] is True
    row = result["rename_shaped"][0]
    assert row["from"] == "aws_s3_bucket.data"
    assert row["to"] == "module.storage.aws_s3_bucket.lake"
    assert row["type"] == "aws_s3_bucket"


def test_a_declared_moved_block_clears_it():
    """The whole point: the operator declares intent Terraform understands, and the gate
    stops objecting."""
    result = address_churn.classify(
        _plan("aws_s3_bucket.data", "module.storage.aws_s3_bucket.lake"),
        moved_blocks=[{"from": "aws_s3_bucket.data", "to": "module.storage.aws_s3_bucket.lake"}])
    assert result["blocked"] is False
    assert result["rename_shaped"] == []
    assert result["covered_by_moved"] == 1


def test_a_genuine_delete_is_not_a_rename():
    """Nothing is created to match it, so this is a real removal -- must not be mislabelled
    as a rename, or real deletions get waved through as 'just a move'."""
    plan = {"resource_changes": [
        {"address": "aws_s3_bucket.old", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["delete"], "before": _BUCKET_ID, "after": None}},
    ]}
    result = address_churn.classify(plan, moved_blocks=[])
    assert result["blocked"] is False
    assert result["rename_shaped"] == []


def test_non_stateful_rename_is_reported_but_not_blocking():
    """Renaming an IAM role recreates it -- disruptive, but no data is lost. Blocking on it
    would train operators to bypass the check."""
    result = address_churn.classify(
        _plan("aws_iam_role.a", "aws_iam_role.b", rtype="aws_iam_role",
              before={"name": "r"}, after={"name": "r"}),
        moved_blocks=[])
    assert result["blocked"] is False
    assert result["advisory_count"] == 1


def test_different_identity_is_not_a_rename():
    """Same type, but a genuinely different bucket -- deleting one and creating another is
    exactly what it looks like."""
    result = address_churn.classify(
        _plan("aws_s3_bucket.a", "aws_s3_bucket.b",
              before={"bucket": "one"}, after={"bucket": "two"}),
        moved_blocks=[])
    assert result["rename_shaped"] == []
    assert result["blocked"] is False


def test_moved_blocks_are_parsed_from_hcl(tmp_path):
    (tmp_path / "moved.tf").write_text('''
moved {
  from = aws_s3_bucket.data
  to   = module.storage.aws_s3_bucket.lake
}
''', encoding="utf-8")
    blocks = address_churn.read_moved_blocks(str(tmp_path))
    assert blocks == [{"from": "aws_s3_bucket.data", "to": "module.storage.aws_s3_bucket.lake"}]


def test_no_moved_blocks_in_an_empty_dir(tmp_path):
    assert address_churn.read_moved_blocks(str(tmp_path)) == []


def test_moved_block_regex_tolerates_quotes_and_extra_whitespace(tmp_path):
    (tmp_path / "a.tf").write_text(
        'moved {\n\n  from  =  module.a.aws_s3_bucket.x\n  to = aws_s3_bucket.y\n}\n',
        encoding="utf-8")
    assert address_churn.read_moved_blocks(str(tmp_path)) == [
        {"from": "module.a.aws_s3_bucket.x", "to": "aws_s3_bucket.y"}]


def test_unreadable_tf_file_does_not_crash_the_scan(tmp_path):
    (tmp_path / "ok.tf").write_text('moved { from = a.b to = c.d }', encoding="utf-8")
    (tmp_path / "notes.md").write_text("not terraform", encoding="utf-8")
    assert address_churn.read_moved_blocks(str(tmp_path)) == [{"from": "a.b", "to": "c.d"}]


# --- Generating the moved blocks, not just demanding them ------------------------------

def test_generated_moved_block_clears_the_churn_it_was_generated_from(tmp_path):
    """The round trip is the whole point: what write_moved() emits must be what
    read_moved_blocks() + classify() then accept as declared."""
    plan = _plan("aws_s3_bucket.data", "module.storage.aws_s3_bucket.lake")
    before = address_churn.classify(plan, moved_blocks=[])
    assert before["blocked"] is True

    path = address_churn.write_moved(str(tmp_path), before)
    assert path is not None

    after = address_churn.classify(
        plan, moved_blocks=address_churn.read_moved_blocks(str(tmp_path)))
    assert after["blocked"] is False
    assert after["covered_by_moved"] == 1


def test_advisory_churn_gets_no_moved_block(tmp_path):
    """An IAM role rename recreates harmlessly. Writing state surgery for it turns a no-op
    into an unreviewed change."""
    result = address_churn.classify(
        _plan("aws_iam_role.a", "aws_iam_role.b", rtype="aws_iam_role",
              before={"name": "etl"}, after={"name": "etl"}), moved_blocks=[])
    assert result["advisory_count"] == 1
    assert address_churn.render_moved(result) == ""
    assert address_churn.write_moved(str(tmp_path), result) is None


def test_write_moved_refuses_to_overwrite_reviewed_state_surgery(tmp_path):
    (tmp_path / "moved.tf").write_text("# hand-written, already reviewed\n", encoding="utf-8")
    result = address_churn.classify(
        _plan("aws_s3_bucket.data", "module.storage.aws_s3_bucket.lake"), moved_blocks=[])
    try:
        address_churn.write_moved(str(tmp_path), result)
    except FileExistsError as exc:
        assert "review and merge by hand" in str(exc)
    else:
        raise AssertionError("an existing moved.tf must never be silently replaced")
    assert (tmp_path / "moved.tf").read_text(encoding="utf-8").startswith("# hand-written")
