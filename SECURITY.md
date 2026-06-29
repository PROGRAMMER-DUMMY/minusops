# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do **not** open a public issue for
security reports.

- Email: security@your-org.example  (replace with your security contact)
- Include: affected version/commit, reproduction steps, and impact.
- We aim to acknowledge within 3 business days and to provide a remediation timeline
  after triage.

## Supported versions

The latest released minor version receives security fixes. Pre-1.0 releases may require
upgrading to the newest version to receive a fix.

## Handling model

- Releases are built in CI and published with a Sigstore-backed build-provenance
  attestation plus a CycloneDX SBOM (see `.github/workflows/release.yml`). Verify the
  attestation before deploying a release artifact.
- The control plane never stores cloud credentials; see
  [`docs/security_model.md`](./docs/security_model.md) for trust boundaries and non-goals.
