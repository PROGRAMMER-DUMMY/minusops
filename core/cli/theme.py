"""
Terminal colour for the CLI, and the rules for when NOT to emit it.

Colour helps a human scan a help screen. It is corruption everywhere else: `\x1b[1m` in a CI
log, a redirected file or a `grep` result is noise somebody eventually writes a sed script to
strip. So the default is off and it turns on only for an interactive terminal.

Precedence, highest first:

  1. `NO_COLOR` set (any value)     -> off. no-color.org. People who set this have a reason --
     a screen reader, a session recorder, a terminal that prints escapes literally -- and an
     opt-out must outrank any opt-in, including ours.
  2. `MINUS_COLOR=1|0`              -> forced on/off. `minusctl --help | less -R` is a real
     workflow, and so is deliberately capturing coloured output.
  3. `TERM=dumb`                    -> off.
  4. `stream.isatty()`              -> the answer.

Every style function takes `enabled` explicitly rather than checking global state, so a caller
decides once per render and the functions stay pure -- which is also what makes them testable
without a fake terminal.

This is ANSI SGR only: no emoji, no box drawing (NFR-01). `visible_width()` exists because
padding a coloured string by `len()` counts the invisible bytes and every column after it
drifts.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/cli/main.py
"""
import os
import re
import sys

RESET = "\x1b[0m"

_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_YELLOW = "\x1b[33m"

NO_COLOR_ENV = "NO_COLOR"
FORCE_COLOR_ENV = "MINUS_COLOR"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def supports_color(stream=None):
    """Whether `stream` can render ANSI. See the module docstring for precedence."""
    if os.environ.get(NO_COLOR_ENV):
        return False

    forced = os.environ.get(FORCE_COLOR_ENV, "").strip().lower()
    if forced in ("1", "true", "yes", "always"):
        return True
    if forced in ("0", "false", "no", "never"):
        return False

    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False

    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 -- a stream without isatty is not a terminal
        return False


def _style(code, text, enabled):
    return f"{code}{text}{RESET}" if enabled else text


def bold(text, enabled=True):
    return _style(_BOLD, text, enabled)


def dim(text, enabled=True):
    return _style(_DIM, text, enabled)


def command(text, enabled=True):
    """A subcommand name -- the thing the reader's eye is hunting for."""
    return _style(_CYAN, text, enabled)


def heading(text, enabled=True):
    """A group title."""
    return _style(_BOLD + _YELLOW, text, enabled)


def visible_width(text):
    """Length as rendered, with escape sequences discounted."""
    return len(_ANSI.sub("", text))
