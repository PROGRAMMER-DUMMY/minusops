# Audit-chain lock fix — scope (root cause accepted, fix not yet implemented)

## Root cause (recap, already accepted)

`_AppendLock.__enter__` acquires via `os.open(lock_path, O_CREAT | O_EXCL | O_WRONLY)` and only
catches `FileExistsError` in its retry loop. On Windows, this call can race against another
thread's `os.remove()` of the same lock filename in `__exit__` — NTFS's delete-then-recreate
semantics for the same path have a real window where a fresh create returns `ERROR_ACCESS_DENIED`
(`PermissionError(13, 'Permission denied')`) instead of either succeeding or reporting
`FileExistsError`. POSIX's atomic unlink-while-open has no equivalent gap. Confirmed empirically:
852/4800 cycles in a tight repro, and 2/8 runs of the real 8×15 test failed locally with the exact
CI signature. `os.remove()` itself never raised it in any repro — only the acquiring `os.open()`.

## Why the broad "just catch PermissionError" fix is wrong

`PermissionError(13)` from `os.open(O_CREAT|O_EXCL)` is genuinely ambiguous — it's raised by this
benign recreate race AND by a real, non-transient problem (a locked file, an ACL denial, an AV
product holding a handle). Catching it broadly in the retry loop trades a fail-loud crash for a
fail-open hang: a genuine permission denial would silently retry for the full timeout and then
report a generic "could not acquire" message indistinguishable from ordinary contention. Under a
tamper-evidence lock, that's a strictly worse failure mode than what exists today.

## Chosen approach: remove the race, don't out-guess its error shape (option 1)

Stop deleting the lock file on release. Create it once (idempotently, never `os.remove()`d
again), and use OS-native advisory **region locking** on that persistent, open file descriptor to
mark acquire/release — `fcntl.flock()` on POSIX, `msvcrt.locking()` on Windows. No delete-recreate
cycle exists, so there is no timing window for Windows to return the wrong error for. This is the
standard idiomatic fix for exactly this class of bug, not a novel design.

```python
import errno
import os
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_SECONDS = 10
_LOCK_POLL_SECONDS = 0.05


class _AppendLock:
    def __init__(self, path):
        self._lock_path = path + _LOCK_SUFFIX
        self._file = None

    def __enter__(self):
        # Opens (creating if absent) ONCE per acquire call -- a genuine failure to even open
        # this file (bad directory, real ACL denial on the directory itself) raises here,
        # immediately, OUTSIDE the retry loop below. This is the fail-loud path: nothing in
        # this call is retried or swallowed.
        self._file = open(self._lock_path, "a+b")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                if os.name == "nt":
                    # msvcrt.locking's OWN documented contention signal for "region already
                    # locked by another process" is OSError with errno == EACCES -- narrow and
                    # specific to THIS call, not the ambiguous multi-cause PermissionError the
                    # old O_CREAT|O_EXCL design raised. Anything else re-raises immediately.
                    try:
                        msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        if exc.errno != errno.EACCES:
                            raise
                        raise _Contended() from None
                else:
                    # flock's own non-blocking contention signal is specifically
                    # BlockingIOError (EWOULDBLOCK/EAGAIN) -- distinct from any other OSError
                    # flock could raise (e.g. a bad fd), which would propagate immediately.
                    try:
                        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise _Contended() from None
                return self
            except _Contended:
                if time.monotonic() >= deadline:
                    self._file.close()
                    raise TimeoutError(
                        f"could not acquire audit-chain lock at {self._lock_path!r} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s -- another writer holds it, or this is a "
                        "persistent permissions/filesystem issue preventing the lock region "
                        "from ever being released"
                    )
                time.sleep(_LOCK_POLL_SECONDS)
            except Exception:
                self._file.close()
                raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if os.name == "nt":
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()


class _Contended(Exception):
    """Internal sentinel: the lock region is held by someone else right now -- always retried
    until the deadline, never confused with a genuine failure to acquire."""
```

(Sketch, not final code -- illustrates the structure, not a diff to apply verbatim.)

### How this preserves fail-loud (the user's core requirement)

Three distinct outcomes, never conflated:
1. **Can't even open the lock file** (bad directory, real ACL denial on the parent dir) — raises
   immediately from `open()`, outside the retry loop entirely. Not retried, not swallowed.
2. **Region currently held by another writer** — the ONLY case that retries, identified by a
   narrow, single-cause signal per platform (`BlockingIOError` on POSIX; `OSError` with
   `errno == EACCES` specifically from `msvcrt.locking`, which is that call's own documented
   meaning for "already locked," not a general-purpose permission error).
3. **Anything else** (a different errno, a different exception entirely) — re-raised
   immediately, not retried, not caught by the contention branch.

This is the structural fix the old design couldn't offer: `os.open(O_CREAT|O_EXCL)` conflated
"create failed because it exists" with "create failed because of a delete race" with "create
failed because of a real permissions problem" all under overlapping exception types on Windows.
Region locking on an already-successfully-opened file separates "could I open the file at all"
from "is the region locked" into two different calls with two different, well-scoped failure
signatures.

### A real behavioral change this surfaces, disclosed not silently changed

The existing crashed-writer test (`test_append_lock_times_out_instead_of_hanging_forever`)
simulates a crash by leaving a bare, empty `.lock` **file** on disk and asserting `append()`
times out rather than hanging. Under OS-level advisory locking, a leftover file with no live
process holding a lock on it is **not contended at all** — a fresh acquire would succeed
immediately, because the OS itself releases a process's locks the moment its file descriptors are
torn down (including on a crash/kill, not just a clean exit). This is a genuine improvement — the
"manually remove a stale `.lock` file after a crash" instruction in today's `TimeoutError` message
becomes unnecessary — but it means that test needs rewriting to hold a **real, live** lock (e.g.
a background thread or subprocess actually calling `flock`/`msvcrt.locking` and holding it) to
still exercise "does `append()` correctly give up rather than hang when the lock is genuinely,
persistently held," since a bare stale file no longer means anything.

## Verification plan (before calling this closed)

1. **Same repro that found the bug, run to a genuinely clean rate**: the 4800-cycle tight stress
   repro used for this diagnosis, and the real `test_append_is_safe_under_concurrent_writers`
   (8×15) run repeatedly (at least 20-30 consecutive runs) on Windows — zero escapes, not "fewer."
2. **Negative control, proving fail-loud is real**: inject a genuine, persistent inability to
   acquire (e.g. a lock path whose parent directory doesn't exist and can't be created, or a
   read-only target) and assert `append()` raises promptly — not after silently spinning the
   full timeout on a real error, and not hanging.
3. **Rewrite the crashed-writer test** to hold a real, live lock (thread/subprocess) rather than
   a bare stale file, proving `append()` still gives up correctly when a lock is genuinely held
   for longer than the timeout, and separately proving a merely-stale leftover file (no live
   holder) no longer needs manual cleanup at all.
4. **Real CI, both Windows jobs green on this test specifically** — not just "the run passed once,"
   the same standard as every other gate this session: prove it in the shipping environment, not
   just locally.

## Not yet implemented

This is the scope for review, matching the same discipline as G2/G5/G6/Phase 4. No code changes
have been made to `audit_chain.py` or its tests. Phase 5 stays held until this is implemented,
verified per the plan above, and closed.
