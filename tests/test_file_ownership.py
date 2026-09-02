"""
Issue #3 / decision #12 -- a team's own .tf files must survive regeneration.

_ensure_empty_or_overwrite was a hard binary: refuse, or clobber. A team that added one
CloudWatch alarm either blocked regeneration forever or lost the alarm.

Fix is a naming convention, not a merge engine. Terraform loads every .tf in a directory, so
ADDITIONS need no merge at all: MinusOps owns and regenerates its own files; anything else in
the directory is the team's and is left strictly alone.
"""

import pytest
import synthesizer


def _workspace(tmp_path, *names):
    d = tmp_path / "terraform"
    d.mkdir()
    for n in names:
        (d / n).write_text(f"# {n}\n", encoding="utf-8")
    return str(d)


def test_empty_directory_is_fine():
    synthesizer._ensure_empty_or_overwrite("/definitely/not/a/dir")  # no raise


def test_a_workspace_of_only_generated_files_regenerates_without_overwrite(tmp_path):
    """MinusOps owns these -- rewriting them is the entire point of regeneration."""
    d = _workspace(tmp_path, "main.tf", "variables.tf", "versions.tf", "providers.tf",
                   "outputs.tf", "minus-generated.json")
    synthesizer._ensure_empty_or_overwrite(d)  # must not raise


def test_a_team_owned_file_does_not_block_regeneration(tmp_path):
    """The regression: one hand-added alarm file must not wedge the workspace."""
    d = _workspace(tmp_path, "main.tf", "variables.tf", "team_alarms.tf")
    synthesizer._ensure_empty_or_overwrite(d)  # must not raise


def test_team_owned_files_are_reported_as_preserved(tmp_path):
    d = _workspace(tmp_path, "main.tf", "team_alarms.tf", "custom_iam.tf")
    preserved = synthesizer.team_owned_files(d)
    assert preserved == ["custom_iam.tf", "team_alarms.tf"]


def test_generated_files_are_not_reported_as_team_owned(tmp_path):
    d = _workspace(tmp_path, "main.tf", "variables.tf", "versions.tf", "providers.tf",
                   "outputs.tf", "minus-generated.json", "terraform.tfstate")
    assert synthesizer.team_owned_files(d) == []


def test_an_unrecognised_non_tf_file_still_requires_review(tmp_path):
    """Fail-safe: something that is neither ours nor a plain .tf addition (a stray state
    file, a half-finished checkout) should still make a human look before we overwrite."""
    d = _workspace(tmp_path, "main.tf")
    (tmp_path / "terraform" / "leftover.zip").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        synthesizer._ensure_empty_or_overwrite(d)
    assert "leftover.zip" in str(exc.value)


def test_overwrite_still_forces_through(tmp_path):
    d = _workspace(tmp_path, "main.tf")
    (tmp_path / "terraform" / "leftover.zip").write_text("x", encoding="utf-8")
    synthesizer._ensure_empty_or_overwrite(d, overwrite=True)  # no raise
