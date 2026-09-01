"""One file must never become two modules.

Every module under core/ is imported flat (`import plan_gate`) via the sys.path bootstrap each
one performs. A module reached instead through the package path (`from core.governance import
plan_gate`) resolves to the SAME FILE but produces a SECOND module object, with its own copy of
every module-level name. Python considers them unrelated.

That is not a style question. `plan_gate._gate_state_lock` becomes two different locks, so the
serialisation protecting gate-state read-modify-write does not hold between the two copies. A
test that sets `plan_gate.LOG_DIR` to a tmp_path leaves the other copy pointing at the real
.agents/logs. Neither failure announces itself.

It was real, not hypothetical: `minusctl doctor` loaded plan_gate and ephemeral_apply twice,
because doctor.py preferred the `core.` package path while everything it shares those modules
with imports flat. These tests are what stop that coming back.

Each check runs in a fresh interpreter rather than the pytest process. Import identity is
global and order-dependent, so asserting it inside a session that has already imported hundreds
of modules would measure the test suite rather than the product.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCAN = '''
import collections, json, os, sys
ROOT = {root!r}
os.chdir(ROOT)
{imports}
by_file = collections.defaultdict(list)
for name, mod in list(sys.modules.items()):
    f = getattr(mod, "__file__", None)
    if not f:
        continue
    f = os.path.abspath(f)
    if f.startswith(os.path.join(ROOT, "core")) or f.startswith(os.path.join(ROOT, "app")):
        by_file[f].append(name)
print(json.dumps({{
    "loaded": len(by_file),
    "duplicates": {{os.path.relpath(f, ROOT): sorted(n)
                    for f, n in by_file.items() if len(n) > 1}},
}}))
'''


def _scan(imports):
    """Import `imports` in a clean interpreter; return what it loaded and what it doubled."""
    proc = subprocess.run(
        [sys.executable, "-c", _SCAN.format(root=ROOT, imports=imports)],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert proc.returncode == 0, f"scan failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_cli_front_door_loads_every_module_once():
    result = _scan("from core.cli import main")
    assert result["loaded"] > 0, "scan imported nothing -- the probe itself is broken"
    assert result["duplicates"] == {}, result["duplicates"]


def test_minusctl_doctor_loads_every_module_once():
    """The regression this file exists for. doctor.py used to try the `core.` package path
    first, so a plain `minusctl doctor` ended up holding two plan_gate objects and two
    ephemeral_apply objects -- including two distinct _gate_state_lock instances."""
    result = _scan("from core.reporting import doctor")
    assert result["duplicates"] == {}, result["duplicates"]

    flat = _scan("import core.reporting.minusctl")  # how the CLI actually reaches doctor
    assert flat["duplicates"] == {}, flat["duplicates"]


def test_mixing_both_import_styles_really_does_double_a_module():
    """The guard above is only meaningful if the thing it guards against is real. Reaching one
    file by both routes on purpose must produce two objects -- if this ever stops being true,
    the tests above are asserting nothing and should be deleted rather than trusted."""
    result = _scan(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(ROOT, 'core', 'governance'))\n"
        "sys.path.insert(0, os.path.join(ROOT, 'core'))\n"
        "import plan_gate\n"
        "from core.governance import plan_gate as pkg\n"
        "assert plan_gate is not pkg\n"
        "assert plan_gate.__file__ == pkg.__file__\n"
    )
    doubled = result["duplicates"]
    assert any(name.endswith("plan_gate.py") for name in doubled), doubled


@pytest.mark.parametrize("entry", [
    "from core.cli import main",
    "from core.reporting import doctor",
    "import core.reporting.minusctl",
])
def test_entry_points_import_without_the_repo_root_on_sys_path(entry):
    """The flat bootstrap must be self-sufficient. These run with cwd at the repo root, which
    puts core/ within reach, but each module is responsible for putting its own siblings on
    sys.path rather than relying on whoever imported it first having done so."""
    result = _scan(entry)
    assert result["loaded"] > 0
