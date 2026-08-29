"""
pii_redactor.py -- Upstream inline PII & PHI inspection and redaction engine.

Inspects tool arguments, prompts, and payloads to sanitize Social Security Numbers (SSN),
Medical Record Numbers (MRN), credit card numbers, email addresses, and phone numbers
before requests reach external LLMs, policy engines, or unprivileged target environments.
"""
import re
import hashlib
from typing import Any, Dict, List, Tuple

# Regex patterns for high-risk PII / PHI
_PATTERNS = {
    "SSN": re.compile(r"\b(?:\d{3}-\d{2}-\d{4}|\d{9})\b"),
    "MRN": re.compile(r"\b(?:MRN-?[A-Z0-9]{6,10}|medical_record_number[\s:=]+([A-Z0-9]+))\b", re.IGNORECASE),
    "CREDIT_CARD": re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b"),
}


def _tokenize(value: str, pii_type: str) -> str:
    """Generate a deterministic token for a redacted value."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"[REDACTED_{pii_type}_{digest}]"


def redact_string(text: str) -> Tuple[str, List[Dict[str, Any]], Dict[str, str]]:
    """
    Redact PII from a single string.
    Returns (redacted_text, findings_list, token_vault_map).
    """
    if not isinstance(text, str) or not text:
        return text, [], {}

    findings = []
    vault = {}
    redacted = text

    for pii_type, pattern in _PATTERNS.items():
        matches = list(pattern.finditer(redacted))
        for match in reversed(matches):
            raw_val = match.group(0)
            token = _tokenize(raw_val, pii_type)
            vault[token] = raw_val
            findings.append({
                "type": pii_type,
                "token": token,
                "start": match.start(),
                "end": match.end()
            })
            redacted = redacted[:match.start()] + token + redacted[match.end():]

    return redacted, findings, vault


def redact_payload(payload: Any) -> Tuple[Any, List[Dict[str, Any]], Dict[str, str]]:
    """
    Recursively inspect and redact strings within dictionaries, lists, or primitives.
    """
    findings = []
    vault = {}

    if isinstance(payload, str):
        r_text, f, v = redact_string(payload)
        findings.extend(f)
        vault.update(v)
        return r_text, findings, vault

    elif isinstance(payload, dict):
        redacted_dict = {}
        for k, v in payload.items():
            r_val, f, vault_sub = redact_payload(v)
            redacted_dict[k] = r_val
            findings.extend(f)
            vault.update(vault_sub)
        return redacted_dict, findings, vault

    elif isinstance(payload, list):
        redacted_list = []
        for item in payload:
            r_val, f, vault_sub = redact_payload(item)
            redacted_list.append(r_val)
            findings.extend(f)
            vault.update(vault_sub)
        return redacted_list, findings, vault

    return payload, findings, vault


def contains_pii(payload: Any) -> bool:
    """Check if a payload contains unredacted PII."""
    _, findings, _ = redact_payload(payload)
    return len(findings) > 0
