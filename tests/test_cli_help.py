"""
`minusctl --help`: every command visible, grouped, described, and coloured when a human is
looking (PRD v10 usability work order).

Before this, `build_parser()` registered five subcommands and buried the other nineteen in an
epilog footnote. An operator reading the help screen concluded the tool was half-built, and
could not discover `create`, `diagnose`, `doctor` or `export` at all.

The colour tests are the ones with teeth, and they are all about NOT emitting escape codes
where nothing can render them. A help screen piped into a file, a CI log, or a `grep` must be
plain text; `\\x1b[1m` in a build log is corruption that someone eventually has to strip.

Depends on: core/cli/main.py, core/cli/theme.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import io
import os
from contextlib import redirect_stdout

import pytest

from cli import main as cli_main
from cli import theme

ESC = "\x1b"


def _help_text():
    out = io.StringIO()
    with redirect_stdout(out):
        with pytest.raises(SystemExit):
            cli_main.main(["--help"])
    return out.getvalue()


class _Stream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


# --- Every command is discoverable ----------------------------------------------------

def test_every_known_command_appears_in_help():
    """The defect this closes. A command absent from `--help` does not exist as far as the
    operator reading that screen is concerned."""
    text = _help_text()

    missing = [c for c in cli_main.known_commands() if c not in text]
    assert not missing, f"absent from the help screen: {missing}"


def test_no_command_is_buried_in_an_epilog_footnote():
    text = _help_text()
    assert "Delegated subcommands:" not in text


def test_every_command_has_a_description_worth_reading():
    """One or two words is a label, not a description. An operator choosing between
    `conformance` and `readiness` needs a sentence."""
    for name, description in cli_main.COMMAND_HELP.items():
        assert len(description) >= 30, f"{name}: {description!r} is too thin to choose from"
        assert description[0].isupper(), f"{name}: description should read as a sentence"


def test_every_command_belongs_to_exactly_one_group():
    grouped = [name for _title, members in cli_main.COMMAND_GROUPS for name in members]

    assert sorted(grouped) == sorted(cli_main.known_commands())
    assert len(grouped) == len(set(grouped)), "a command is listed in two groups"


def test_the_group_headings_are_rendered():
    text = _help_text()
    for title, _members in cli_main.COMMAND_GROUPS:
        assert title in text


def test_commands_appear_under_their_own_group():
    """Grouping is only useful if the membership is real."""
    text = _help_text()
    positions = {title: text.index(title) for title, _ in cli_main.COMMAND_GROUPS}
    ordered = sorted(positions.values())

    for index, (title, members) in enumerate(cli_main.COMMAND_GROUPS):
        start = positions[title]
        following = [p for p in ordered if p > start]
        end = following[0] if following else len(text)
        block = text[start:end]
        for name in members:
            assert f"  {name}" in block, f"{name} is not under {title}"


def test_the_usage_line_does_not_dump_every_command_into_braces():
    """`{use,runs,gate,...}` across 24 commands is an unreadable wall."""
    text = _help_text()
    usage = text.splitlines()[0] if text.splitlines() else ""
    assert "{" not in usage


def test_help_carries_no_emoji():
    """NFR-01."""
    assert all(ord(ch) < 0x2190 for ch in _help_text())


def test_bare_invocation_prints_the_same_help():
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_main.main([])

    assert code != 0
    assert "create" in out.getvalue()


# --- Colour: only where something can render it ---------------------------------------

def test_colour_is_off_when_output_is_not_a_terminal(monkeypatch):
    """The one that matters. Piped into a file, a CI log or `grep`, the help must be plain
    text -- an escape code in a build log is corruption someone later has to strip."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MINUS_COLOR", raising=False)

    assert theme.supports_color(_Stream(tty=False)) is False


def test_colour_is_on_for_an_interactive_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MINUS_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert theme.supports_color(_Stream(tty=True)) is True


def test_no_color_is_respected_even_on_a_terminal(monkeypatch):
    """no-color.org. Users who set it have a reason -- a screen reader, a recorder, a
    terminal that renders escapes literally."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("MINUS_COLOR", raising=False)

    assert theme.supports_color(_Stream(tty=True)) is False


def test_a_dumb_terminal_gets_no_colour(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MINUS_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")

    assert theme.supports_color(_Stream(tty=True)) is False


def test_colour_can_be_forced_for_a_pipe(monkeypatch):
    """`minusctl --help | less -R` is a real workflow, and so is capturing coloured output
    deliberately."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("MINUS_COLOR", "1")

    assert theme.supports_color(_Stream(tty=False)) is True


def test_no_color_beats_a_force_request(monkeypatch):
    """An explicit opt-out outranks an opt-in; the user who set NO_COLOR cannot see the
    output any other way."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("MINUS_COLOR", "1")

    assert theme.supports_color(_Stream(tty=True)) is False


def test_styles_are_pass_through_when_colour_is_off():
    assert theme.bold("gate", enabled=False) == "gate"
    assert theme.heading("Deploy", enabled=False) == "Deploy"
    assert ESC not in theme.dim("note", enabled=False)


def test_styles_emit_escape_codes_when_colour_is_on():
    coloured = theme.bold("gate", enabled=True)

    assert ESC in coloured
    assert coloured.endswith(theme.RESET)
    assert "gate" in coloured


def test_the_captured_help_screen_has_no_escape_codes():
    """redirect_stdout gives a StringIO, which is not a tty -- so the help every other test
    in this suite reads must be plain."""
    assert ESC not in _help_text()


def test_width_is_measured_without_the_escape_codes():
    """Column alignment breaks if padding counts invisible bytes."""
    assert theme.visible_width(theme.bold("gate", enabled=True)) == len("gate")
    assert theme.visible_width("gate") == len("gate")


# --- Dispatch is unchanged ------------------------------------------------------------

def test_a_delegated_command_still_reaches_the_legacy_cli(monkeypatch):
    """Zero regressions: the subparsers exist so the command is VISIBLE, and dispatch still
    hands the whole argv to the implementation that owns it."""
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main, "_delegate", _spy)
    code = cli_main.main(["create", "a pipeline", "--json"])

    assert code == 0
    assert seen["argv"] == ["create", "a pipeline", "--json"]


def test_delegated_help_passes_through_to_the_owning_command(monkeypatch):
    """`minusctl create --help` must show create's real flags, not this package's summary."""
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main, "_delegate", _spy)
    cli_main.main(["create", "--help"])

    assert seen["argv"] == ["create", "--help"]


def test_a_native_command_is_still_parsed_here(monkeypatch, tmp_path):
    import runs
    from cli import context as cli_context

    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli_context, "WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_main.main(["runs", "list"])

    assert code == 0
    assert "no runs yet" in out.getvalue()


def test_colour_wraps_the_name_only_not_its_padding():
    """Colouring the PADDED label paints a block out to the column edge instead of
    highlighting the word. Checked against a short name -- `conformance` is the longest
    command and has no padding, so the bug cannot show there."""
    text = cli_main.format_help(enabled=True)

    line = next(l for l in text.splitlines() if l.lstrip().startswith(ESC) and "use" in l)
    span = line[line.index(ESC):line.index(theme.RESET) + len(theme.RESET)]
    inner = span[len(ESC + "[36m"):-len(theme.RESET)]

    assert inner == "use", f"the coloured span is {inner!r}, not the bare name"


def test_columns_stay_aligned_with_colour_on():
    """Padding is computed on the raw name, so descriptions land in one column whether or not
    the escapes are present."""
    def _columns(text):
        starts = set()
        for line in text.splitlines():
            if not line.startswith("  ") or theme.visible_width(line) < 4:
                continue
            plain = theme._ANSI.sub("", line)
            if "  " in plain[2:].rstrip():
                starts.add(len(plain) - len(plain[2:].split("  ", 1)[-1]))
        return starts

    assert _columns(cli_main.format_help(enabled=False)) ==         _columns(cli_main.format_help(enabled=True))
