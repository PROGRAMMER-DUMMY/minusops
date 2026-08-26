# Tools Context Index

This document provides exhaustive context for all CLI helper scripts and diagnostic utilities within the [`tools`](.) directory.

---

## 1. Environment & System Diagnostics

- [`tools/doctor.ps1`](./doctor.ps1): PowerShell environment diagnostic script. Verifies local tool dependencies (Terraform, AWS CLI, Python), checks AWS STS caller identity, and confirms system readiness.

  **Superseded (MINUS-107).** This script only runs under Windows PowerShell, so it could not run in CI containers, on macOS, or on Linux. The cross-platform replacement is [`core/reporting/doctor.py`](../core/reporting/doctor.py), invoked as `minusctl doctor [--json]`. It checks everything this script did plus OPA, the external policy scanners, the Python packages, and **credential posture** (long-term or root credentials are reported as a warning, not an OK). Prefer `minusctl doctor` everywhere; `doctor.ps1` is kept only for operators with an existing muscle-memory shortcut and receives no new checks.
