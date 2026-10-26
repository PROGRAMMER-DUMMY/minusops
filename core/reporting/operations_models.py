"""Small shared trust-boundary helpers for operations evidence (not authorization)."""
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path


class OperationsError(ValueError):
    pass


def load_json(path, *, max_bytes=1048576):
    """Bounded object parser with duplicate-key rejection at evidence boundaries."""
    with Path(path).open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    return parse_json(raw, max_bytes=max_bytes)


def parse_json(raw, *, max_bytes=1048576):
    """The same bounded parser for captured HTTP/file bytes; never a path or authority."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OperationsError("duplicate JSON key")
            result[key] = value
        return result
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise OperationsError("JSON input exceeds byte limit")
    return bounded_record(json.loads(raw, object_pairs_hook=pairs), max_bytes=max_bytes)


def local_path(root, path):
    """Require lexical containment and no existing linked/reparse components."""
    root = Path(root).absolute()
    path = Path(path).absolute()
    if not path.is_relative_to(root):
        raise OperationsError("path escapes permitted workspace")
    for item in (root, *[root.joinpath(*path.relative_to(root).parts[:i])
                         for i in range(1, len(path.relative_to(root).parts) + 1)]):
        if item.is_symlink() or (item.exists() and item.resolve() != item):
            raise OperationsError("linked path refused")
        if item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 0x400:
            raise OperationsError("reparse path refused")
    return path


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_timestamp(value):
    if isinstance(value, dt.datetime):
        stamp = value
    else:
        try:
            stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise OperationsError("aware UTC timestamp required") from None
    if stamp.utcoffset() != dt.timedelta(0):
        raise OperationsError("aware UTC timestamp required")
    return stamp


def bounded_record(value, *, max_bytes=1048576):
    """Reject non-JSON data, nonfinite numbers and resource-exhausting input."""
    def walk(item, depth=0):
        if depth > 16:
            raise OperationsError("record nesting limit exceeded")
        if isinstance(item, dict):
            if len(item) > 256 or any(not isinstance(key, str) or len(key) > 256 for key in item):
                raise OperationsError("invalid record keys")
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 10000:
                raise OperationsError("record collection limit exceeded")
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > max_bytes:
                raise OperationsError("record string limit exceeded")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise OperationsError("finite numbers required")
        elif item is not None and not isinstance(item, (bool, int)):
            raise OperationsError("JSON data required")
    walk(value)
    if not isinstance(value, dict) or len(canonical_json(value).encode()) > max_bytes:
        raise OperationsError("record exceeds byte limit or is not an object")
    return value


def identifier(value, name="identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value):
        raise OperationsError("invalid " + name)
    return value


def file_identifier(value, name="file identifier"):
    identifier(value, name)
    stem = value.split(".", 1)[0].upper()
    if (":" in value or value.endswith(".") or stem in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"COM[1-9]|LPT[1-9]", stem)):
        raise OperationsError("invalid " + name)
    return value


def validate_scope(value, *, actionable=False):
    bounded_record(value, max_bytes=32768)
    for key in ("tenant_id", "service_id", "workload_id"):
        identifier(value.get(key), key)
    if value.get("run_id") is not None:
        identifier(value["run_id"], "run_id")
    resources = value.get("resource_ids", [])
    if not isinstance(resources, list) or len(resources) > 256 or any(
            not isinstance(item, str) or not item or len(item) > 2048 for item in resources):
        raise OperationsError("invalid resource scope")
    if actionable and (value.get("provider") != "aws" or value.get("partition") not in
                       ("aws", "aws-us-gov", "aws-cn") or not re.fullmatch(
                           r"[0-9]{12}", str(value.get("account_id", ""))) or not re.fullmatch(
                               r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", str(value.get("region", "")))):
        raise OperationsError("complete AWS action scope required")
    return dict(value)


def validate_evidence_ref(value):
    bounded_record(value, max_bytes=16384)
    identifier(value.get("evidence_id"), "evidence_id")
    identifier(value.get("source_id"), "source_id")
    if not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", ""))):
        raise OperationsError("evidence digest required")
    parse_timestamp(value.get("observed_at"))
    if value.get("valid_until") is not None:
        parse_timestamp(value["valid_until"])
    if value.get("sanitized") is not True:
        raise OperationsError("sanitized evidence required")
    return dict(value)
