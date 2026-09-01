"""
`minusctl console` -- serve the visual governance console.

Thin by design, exactly like `gate.py`: the console decides nothing, so a wrapper that grew
behaviour would be a second place governance rules could live. This resolves the run through
the same fail-closed context hierarchy every other command uses and hands off.

The lifecycle half exists because launching twice used to raise `Address already in use`.
It probes the port first, and the distinction it draws is the point: SOMETHING listening is
not OUR console. A bare TCP accept proves a socket is open, nothing more, so the probe asks
the server to identify itself and only reuses one that answers. Opening a browser at whoever
happens to hold 8050 would hand the operator someone else's application and report success.

Aliases `ui` and `dashboard` exist because `dashboard` is what operators typed for a year;
pointing it at the console is kinder than an unknown-command error, and it is how the
deprecation actually reaches people.

Depends on: core/cli/context.py, app/console_app.py
Shells out to: nothing. Standard library only.
Used by: core/cli/main.py
"""
import os
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser

from .. import context

ALIASES = ("ui", "dashboard")

PID_FILE = os.path.join(".minus", "console.pid")

# The console serves a Dash page whose title carries the product name. Cheap to check and
# specific enough that another Flask app on the same port will not answer to it.
_IDENTITY_MARKER = "minusops"
_PROBE_TIMEOUT = 1.5


def add_parser(subparsers):
    # `ALIASES` was declared and never passed to argparse, so `minusctl ui` and
    # `minusctl dashboard` were documented in the source and refused at the prompt.
    parser = subparsers.add_parser(
        "console", aliases=list(ALIASES),
        help="Serve the visual governance console for a run.")
    parser.add_argument("action", nargs="?", choices=("start", "stop"), default="start",
                        help="start (default) or stop a background console")
    parser.add_argument("--run", help="Run workspace to open; defaults to the active run")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--background", "--daemon", dest="background", action="store_true",
                        help="Serve in the background and record the PID under .minus/")
    parser.add_argument("--no-browser", dest="browser", action="store_false",
                        help="Do not open a browser when reusing a running console")
    return parser


def port_in_use(host, port):
    """Whether anything accepts a TCP connection there. Says nothing about what."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(_PROBE_TIMEOUT)
    try:
        return probe.connect_ex((host, port)) == 0
    finally:
        probe.close()


def _probe_identity(host, port):
    """Fetch the served page and return its text, or "" when it cannot be read.

    Never raises. A refused connection, a timeout and a server that speaks a protocol this
    does not understand are all "cannot identify", which is the safe answer.
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/",
                                    timeout=_PROBE_TIMEOUT) as response:
            return response.read(4096).decode("utf-8", errors="replace").lower()
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def is_our_console(host, port):
    """True only when the server on that port identifies itself as this console."""
    return _IDENTITY_MARKER in _probe_identity(host, port)


def _serve(host, port):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from app import console_app
    return console_app.main(["--host", host, "--port", str(port)])


def _pid_path(workspace=None):
    return os.path.join(workspace or os.getcwd(), PID_FILE)


def _write_pid(pid, workspace=None):
    path = _pid_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(str(pid) + "\n")


def _terminate(pid):
    """Signal a process to stop. Returns False when it was not there to signal."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def launch(host="127.0.0.1", port=8050, run_id=None, background=False, browser=True,
           workspace=None):
    """Serve, or reuse a console that is already serving.

    Three outcomes, kept distinct because they call for different operator actions:
    ours is running (reuse it), something else holds the port (report it, change nothing),
    or the port is free (serve).
    """
    url = f"http://{host}:{port}"
    if port_in_use(host, port):
        if is_our_console(host, port):
            print(f"[console] MinusOps console is already running on {url}")
            if browser:
                webbrowser.open(url)
            return 0
        sys.stderr.write(
            f"Error: port {port} is in use, and the server there is not the MinusOps "
            f"console.\n       Stop it, or choose another port with --port.\n")
        return 1

    if background:
        process = subprocess.Popen(
            [sys.executable, "-m", "core.cli.main", "console",
             "--host", host, "--port", str(port)],
            cwd=workspace or os.getcwd())
        _write_pid(process.pid, workspace)
        print(f"[console] serving in the background on {url} (pid {process.pid})")
        if browser:
            webbrowser.open(url)
        return 0

    if run_id:
        print(f"[console] run: {run_id}")
    return _serve(host, port)


def stop(workspace=None):
    """Stop a background console recorded under .minus/console.pid."""
    path = _pid_path(workspace)
    if not os.path.exists(path):
        sys.stderr.write("Error: no console PID is recorded; nothing to stop.\n")
        return 1

    try:
        with open(path, encoding="utf-8") as handle:
            pid = int(handle.read().strip())
    except (ValueError, OSError):
        # Never signal an integer parsed out of a corrupt file: it may name a live process
        # that has nothing to do with this console.
        sys.stderr.write(f"Error: {path} does not contain a process id; not signalling "
                         f"anything. Remove it by hand.\n")
        return 1

    stopped = _terminate(pid)
    try:
        os.remove(path)
    except OSError:
        pass
    if stopped:
        print(f"[console] stopped (pid {pid})")
        return 0
    # A stale file otherwise blocks every later start with a console that is not there.
    print(f"[console] no process {pid} was running; cleared the stale PID file")
    return 0


def run(args):
    """Resolve the run, then serve. Refuses rather than guessing which run to show."""
    if getattr(args, "action", "start") == "stop":
        return stop()

    try:
        record = context.resolve_run(getattr(args, "run", None))
    except context.ContextError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    return launch(host=args.host, port=args.port, run_id=record.get("run_id"),
                  background=getattr(args, "background", False),
                  browser=getattr(args, "browser", True))
