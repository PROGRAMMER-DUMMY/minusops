# Native Sim internal preview build inputs

This isolated branch builds the pinned native Sim application with the MinusOps
Apache source overlays. It is a build payload, not the full MinusOps application
or a branch intended to merge into main.

The only workflow is `.github/workflows/native-sim-preview.yml`. It checks public
source/overlay hashes, contract tests and owned TypeScript before compiling, then
stores successful output in a new private GHCR package. Build logs are public;
no provider key, AWS credential, private run or local configuration is supplied.
The upstream tree includes unchanged enterprise files, so the resulting image
stays private for internal nonproduction evaluation.

Local Chat gateway, OS vault, runtime settings and browser acceptance remain in
the operator's existing workspace. A build result does not establish those checks.
See `app/sim-native/NOTICE.md` for attribution and modified source identification.
