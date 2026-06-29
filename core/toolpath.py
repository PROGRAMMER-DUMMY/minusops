"""
Cross-platform discovery of external CLIs (terraform, aws, headless browsers).

This module never hardcodes a specific user's home directory. On Windows it
refreshes the current process PATH from the registry — winget / MSI installers
update the registry but not the already-running session — and then searches
standard, generic install locations. The discovery runs at most once per process.
"""
import glob
import os
import shutil
import sys

_ensured = False


def _windows_extra_dirs():
    """Generic Windows install locations — derived from environment, never a fixed user path."""
    extra = []
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        # winget installs Terraform under a versioned package dir; match it generically.
        extra += glob.glob(os.path.join(
            localappdata, "Microsoft", "WinGet", "Packages", "Hashicorp.Terraform_*"))
    for program_files in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if program_files:
            extra.append(os.path.join(program_files, "Amazon", "AWSCLIV2"))
    return [d for d in extra if os.path.isdir(d)]


def _refresh_windows_path():
    if os.name != "nt":
        return
    try:
        import winreg
        values = []
        for root, sub in (
            (winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ):
            try:
                with winreg.OpenKey(root, sub) as key:
                    raw, _ = winreg.QueryValueEx(key, "Path")
                    if raw:
                        values.append(raw)
            except OSError:
                continue
        if values:
            merged = os.pathsep.join(values + [os.environ.get("PATH", "")])
            os.environ["PATH"] = os.path.expandvars(merged)
    except Exception as exc:  # pragma: no cover - registry is environment-specific
        print(f"[toolpath] warning: could not refresh PATH from registry: {exc}", file=sys.stderr)

    current = os.environ.get("PATH", "").split(os.pathsep)
    add = [d for d in _windows_extra_dirs() if d not in current]
    if add:
        os.environ["PATH"] += os.pathsep + os.pathsep.join(add)


def ensure_external_tools():
    """Make terraform / aws discoverable on PATH for this process. Idempotent."""
    global _ensured
    if _ensured:
        return
    _refresh_windows_path()
    _ensured = True


def find_tool(name, extra_candidates=()):
    """Return an absolute path to a CLI, or None. Searches PATH then generic locations."""
    ensure_external_tools()
    found = shutil.which(name)
    if found:
        return found
    candidates = list(extra_candidates)
    if os.name == "nt":
        for directory in _windows_extra_dirs():
            candidates.append(os.path.join(directory, name + ".exe"))
            candidates.append(os.path.join(directory, name))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None
