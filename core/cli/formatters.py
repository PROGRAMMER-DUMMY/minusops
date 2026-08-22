"""
Terminal output: ASCII tables and the run specification card.

Two constraints shape everything here, and both are NFR-01 in PRD v7:

  * No emoji, and no box-drawing characters either. These outputs get pasted into tickets,
    chat and CI logs, and a terminal that cannot render U+2502 turns a table into noise. The
    `_ascii_only` guard makes that a checked property rather than a convention.
  * `None` renders as `-`, never as the word "None" and never as `0`. A missing BCM estimate
    shown as `$0.00` reads as "this pipeline is free"; the same doctrine as
    core/cost/budget_calculator.py and the run registry.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/cli/commands/runs.py, core/cli/commands/source.py, core/cli/main.py
"""
EMPTY = "-"
NOT_PRICED = "unpriced"

# Everything a terminal is guaranteed to render. Anything above this is an arrow, a box,
# a dingbat or an emoji.
_MAX_ORD = 0x2190


def _ascii_only(text):
    return "".join(ch if ord(ch) < _MAX_ORD else "?" for ch in str(text))


def cell(value):
    """One table cell. None and empty string both become `-`; False and 0 do not."""
    if value is None or value == "":
        return EMPTY
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or EMPTY
    return _ascii_only(value)


def table(headers, rows):
    """Left-aligned, space-padded, pipe-separated. Header renders even with no rows."""
    headers = [_ascii_only(h) for h in headers]
    body = [[cell(value) for value in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def _line(values):
        return "| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(values)) + " |"

    lines = [_line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(_line(row) for row in body)
    return "\n".join(lines)


def card(title, sections):
    """A run specification card: `sections` is [(heading, [(label, value), ...]), ...].

    Headings render as `[Heading]` (PRD v6 FR-03) so a section is scannable in a wall of
    plain text without colour."""
    title = _ascii_only(title)
    rule = "=" * max(len(title), 60)
    lines = [rule, title, rule, ""]
    for heading, fields in sections:
        lines.append(f"[{_ascii_only(heading)}]")
        width = max((len(label) for label, _ in fields), default=0)
        for label, value in fields:
            lines.append(f"  {_ascii_only(label).ljust(width)}  {cell(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def money(value):
    """A cost, or an honest statement that none was measured. Never `$0.00` for unknown."""
    if value is None:
        return NOT_PRICED
    return f"${float(value):,.2f}"
