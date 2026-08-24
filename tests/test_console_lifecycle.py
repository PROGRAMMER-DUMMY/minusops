"""
PRD v15 WP-03/WP-05: single-instance console lifecycle.

The property worth holding is not "the port is busy" but "OUR console is already there".
Those are different facts: something else on 8050 is a reason to report a conflict, not a
reason to open a browser at it and call the job done.

Depends on: core/cli/commands/console.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os
import socket

import pytest

from core.cli.commands import console as console_cmd


@pytest.fixture()
def listening_port():
    """A real socket on an ephemeral port, so the probe is exercised rather than mocked."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    yield server.getsockname()[1]
    server.close()


def test_a_free_port_is_reported_as_free():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    assert not console_cmd.port_in_use("127.0.0.1", port)


def test_a_listening_port_is_detected(listening_port):
    assert console_cmd.port_in_use("127.0.0.1", listening_port)


def test_something_listening_is_not_assumed_to_be_our_console(listening_port):
    """A bare TCP accept is not the console. Opening a browser at whatever happens to hold
    8050 would hand the operator someone else's application and report success."""
    assert not console_cmd.is_our_console("127.0.0.1", listening_port)


def test_our_console_is_identified_by_its_own_response(monkeypatch):
    monkeypatch.setattr(console_cmd, "_probe_identity", lambda host, port: "minusops-console")

    assert console_cmd.is_our_console("127.0.0.1", 8050)


def test_a_second_launch_reuses_the_running_console_and_opens_a_browser(monkeypatch, capsys):
    """FR-03. Running `minusctl console` twice must not raise a port binding error, and must
    not start a second server."""
    opened, served = [], []
    monkeypatch.setattr(console_cmd, "port_in_use", lambda host, port: True)
    monkeypatch.setattr(console_cmd, "is_our_console", lambda host, port: True)
    monkeypatch.setattr(console_cmd.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(console_cmd, "_serve", lambda *a, **k: served.append(a) or 0)

    rc = console_cmd.launch(host="127.0.0.1", port=8050, run_id="r1")

    assert rc == 0
    assert served == [], "a second server was started"
    assert opened == ["http://127.0.0.1:8050"]
    assert "already running" in capsys.readouterr().out


def test_a_foreign_process_on_the_port_is_reported_not_reused(monkeypatch, capsys):
    """The port is taken but not by us. Reusing it would open the wrong application;
    starting a server would raise. The honest outcome is a non-zero exit that says so."""
    opened = []
    monkeypatch.setattr(console_cmd, "port_in_use", lambda host, port: True)
    monkeypatch.setattr(console_cmd, "is_our_console", lambda host, port: False)
    monkeypatch.setattr(console_cmd.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(console_cmd, "_serve", lambda *a, **k: 0)

    rc = console_cmd.launch(host="127.0.0.1", port=8050, run_id="r1")

    assert rc != 0
    assert opened == [], "a browser was opened at someone else's server"
    assert "not the MinusOps console" in capsys.readouterr().err


def test_a_free_port_serves_normally(monkeypatch):
    served = []
    monkeypatch.setattr(console_cmd, "port_in_use", lambda host, port: False)
    monkeypatch.setattr(console_cmd, "_serve", lambda host, port: served.append((host, port)) or 0)

    assert console_cmd.launch(host="127.0.0.1", port=8050, run_id="r1") == 0
    assert served == [("127.0.0.1", 8050)]


# --- PID tracking and stop ----------------------------------------------------------------

def test_stop_reports_when_no_console_is_recorded(tmp_path, capsys):
    rc = console_cmd.stop(workspace=str(tmp_path))

    assert rc != 0
    assert "no console" in capsys.readouterr().err.lower()


def test_stop_refuses_a_pid_file_that_is_not_a_pid(tmp_path, capsys):
    """A corrupt pid file must not become a signal sent to whatever integer it parsed to."""
    pid_dir = tmp_path / ".minus"
    pid_dir.mkdir()
    (pid_dir / "console.pid").write_text("not-a-pid", encoding="utf-8")

    assert console_cmd.stop(workspace=str(tmp_path)) != 0


def test_stop_removes_the_pid_file_of_a_process_that_is_already_gone(tmp_path, monkeypatch):
    """A stale pid file otherwise blocks every later start with a console that is not there."""
    pid_dir = tmp_path / ".minus"
    pid_dir.mkdir()
    (pid_dir / "console.pid").write_text("999999", encoding="utf-8")
    monkeypatch.setattr(console_cmd, "_terminate", lambda pid: False)

    console_cmd.stop(workspace=str(tmp_path))

    assert not (pid_dir / "console.pid").exists()


def test_the_lifecycle_module_uses_only_the_standard_library():
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "cli", "commands", "console.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"os", "re", "signal", "socket", "subprocess", "sys", "urllib",
                        "webbrowser", "context", "app"}, imported


def test_the_lifecycle_module_carries_no_emoji():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "cli", "commands", "console.py")

    assert all(ord(ch) < 128 for ch in open(path, encoding="utf-8").read())
