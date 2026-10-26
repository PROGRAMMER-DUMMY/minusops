# OpenRouter connection acceptance, 2026-10-27

The native source connection is implemented. Native browser and real-provider
acceptance remain incomplete; no modified native app is running yet.

The approved [CI build](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40374089795)
uses isolated commit `6f6b38c19d9c145385be48d09e5ed866df18e0b6` in the public
`build/native-sim-openrouter` history. Its contract and owned TypeScript checks
passed in that run, followed by native compilation and private draft export.
Its CI configuration digest is
`sha256:dbf07c50b79552020bcd6a0d2e969be0d7334972014b01dbe41c1e145cd47496`.
Authenticated draft checks and anonymous 404 checks passed for the export.
The separate [range preparation](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40382176415)
verified the original parts and prepared checksums for bounded local transfer.
The first import stopped at the 3 GiB host-storage floor after 13 of 20 verified
ranges. After scoped build-cache cleanup and approved compaction, all 20 ranges
and original part hashes passed and Docker loaded the image. Its local OCI manifest
is `sha256:7c2c32acb0189250e6b90617a1a30f96cb0bb82ba90da13caac22be286c9ae47`.
That manifest's configuration digest matches CI, and all 46 filesystem layer
identities match the configuration. The temporary private draft was deleted.
Native browser testing reached the app but workspace creation returned HTTP 500.
Next's `generate-env` pass corrupted native `env.NEXT_PUBLIC_*` references into
invalid JavaScript; that app was stopped. The compile-only correction passed all
32 native contracts and a small real Next runtime fixture including native-style
configuration access, model routes and an API POST. A replacement full native
build and browser acceptance remain pending. CI now parses each generated server
JavaScript file in a fresh bounded process before exporting it.

The prior native image built successfully. Its GHCR publication was public and
was deleted by the [quarantine workflow](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40372689852).
Fresh anonymous access returned 403. No user keys or run data were included;
external download history remains unknown. Registry publishing is removed.
The replacement private-draft transport was checked with a harmless asset:
authenticated download matched, while anonymous release/API/browser access
returned 404. The same check passed in actual CI before building.

Checked against Sim `df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5`:

- 232 assistant, protocol, gateway, durable runtime and module identity checks
  passed earlier; the backend is unchanged since that result.
- All 32 native proxy, overlay, completion, config, route-scope and compiler checks
  passed in bounded Docker with injected auth and HTTP boundaries.
- The separate CI type check passed for 18 owned files with zero owned/config
  diagnostics and zero unrelated diagnostics. It is not a full application check.
- 52 Python preparation, documentation and module identity checks passed,
  including 19 build/local helper cases.
- Preparation verifies all 19 Apache overlay hashes and the browser adapter hash,
  preserves upstream notices and creates no private configuration in build-only mode.
- The earlier controlled HTTP check used an isolated synthetic key in the actual
  Windows vault. Save/delete, model preferences, SSE completion, identical replay
  and a second turn with three history messages passed. That key was removed.
  This does not establish native browser or live-provider acceptance.

Build and runtime launch Next from `apps/sim` with an absolute CLI path. This
corrects the previous build's font lookup at `/app/public` after successful
compilation. The compiler regression fixture checks its actual child directory,
nonzero exit forwarding and signal reporting. Webpack fixtures cover native
browser/server boundaries, PDF worker assets and Monaco's ESM entry.

The later full build hit its 20-minute limit; its captured stage log was not
printed on that exception, so its final stage remains unknown. That diagnostics
gap is fixed and tested. The current preview uses experimental `compile`
and serves requests dynamically. A tiny pinned-Next fixture
passed page and API POST serving through a proxy with that sequence. It does not
establish native Sim or production acceptance.

The following native attempt compiled successfully in 7.3 minutes, then exhausted
the 12 GiB heap while collecting packaging dependency traces. The current
build-only, version-checked trace adapter omits that collector for the complete
source/dependency image. It is manifest-bound and absent from runtime startup;
standalone output remains refused. With the adapter, the small real Next fixture
passed page, model-path and API POST checks. The full native image subsequently
built successfully in CI; native browser evidence is still pending.

Local full compilation remains stopped after earlier memory/storage failures.
Docker has its original approximately 8 GB allocation, with no WSL override.
CI uses 12 GiB compiler RAM plus up to 8 GiB swap on its disposable hosted runner;
other hosts receive at most 2 GiB swap. Type checking and compilation run separately.
Before the disk-space stop, DB, Redis and realtime were healthy, migrations
exited zero, and the vault-backed Windows gateway was reachable from a container.
The gateway was restored after the failed browser check, and its isolated synthetic
vault entry is absent. The native app is stopped pending the corrected image.
Existing database, uploads and private settings are preserved. Final Windows
elevation was canceled and was not retried; host storage remains constrained.

The prior wheel packaging check passed but must be refreshed after final source
and acceptance updates. It excluded upstream EE, source archives, private state
and the removed API; all manifest-bound overlays matched their maintained bytes.

No live provider request or paid inference ran. This connection supports text
conversation only; native workflow tools, infrastructure editing and deployment
are outside its implemented scope. Authentication and production hosting remain
separate qualification work. See [setup instructions](./README.md).
