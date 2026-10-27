# Native Sim OpenRouter preview

The full native Sim application is running at `http://localhost:3010/workspace`,
with its original controls and palette. Key entry, model/effort selection, two
streamed Chat turns, history reload, canvas position persistence and model-directory
search passed controlled browser checks. See [acceptance](./ACCEPTANCE.md) for exact
evidence and limits. Real OpenRouter inference still requires the user's key.

In the desired workspace, open **Settings -> BYOK -> OpenRouter Chat -> Add Key**.
Select a model in the Chat composer, then an advertised reasoning effort if the
model offers one. The native model directory is at `http://localhost:3010/models`.
That directory is the upstream catalog; the Chat picker uses the credential-scoped
OpenRouter catalog. Model listing alone does not prove inference access.
The picker preserves catalog IDs including a leading `~` alias marker. The
current local preview includes the catalog-validation fix described in
[acceptance](./ACCEPTANCE.md); a browser hard refresh may be needed after updating.

This is an internal nonproduction integration pinned to the
[Sim source at df3db64](https://github.com/simstudioai/sim/tree/df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5).
The [manifest](./patch-manifest.json) binds 24 Apache overlay files and the original
hashes of changed upstream files. The maintained directory does not vendor the
upstream/EE tree. The private runtime image retains unchanged upstream files;
keep it internal. [NOTICE.md](./NOTICE.md) records attribution.

The `MINUS_OPENROUTER_CHAT=true` flag exposes this native BYOK section and replaces
the hosted Chat model control with the local selector. It does not enable
enterprise features. This connection supports text conversation; architecture
editing, workflow actions, source publication and infrastructure deployment are
not implemented by this worker. Native manual workflow editing is separate.
CLI architecture canvas/console routing still uses the component application.
The native architecture route, source proposal review and selected Chat context are
being integrated under [the implementation plan](../../docs/native-sim-implementation-plan.md).
Their source/adapter tests are separate from compiled-image and browser acceptance.

Keys pass transiently through the authenticated Next proxy to the host OS vault.
They do not enter Sim's database, browser persistence, public preferences or
runtime environment files through this Chat integration. Its vault namespace is
`MinusOps.ai_assistant.<SHA256 of canonical gateway workspace path>`, entry
`openrouter`. Ambient provider keys are not a fallback.

The native `assistant/{status,key,models,preferences}` routes under each workspace
use a fixed local gateway and the server-only bridge token. Writes require
workspace admin and same origin. The 3010 preview uses
`NEXT_PUBLIC_APP_URL=http://localhost:3010` across Docker port mapping. Preferences
are independent `{model,effort,max_tokens}` values; the worker freezes them per
turn. Unsupported models/efforts and missing credentials refuse without fallback.
Provider diagnostics are not forwarded to the browser.

For an already prepared preview, reuse the existing gateway. If it is stopped,
start it in a separate terminal from this checkout:

```powershell
python -m core.integrations.sim_assistant_gateway --workspace . --token-file .minus/native-sim/preview.env
```

Then start the installed image and its existing services:

```powershell
docker compose --project-name minusops-native-sim --env-file .minus/native-sim/preview.env -f artifacts/runtime/native-sim/compose.json -f artifacts/runtime/native-sim/openrouter-compose.json up -d simstudio_openrouter realtime_openrouter
```

The native app and realtime bind only to loopback at ports 3010 and 3012. Database,
uploads, transport and encryption configuration are retained. Do not remove
volumes or regenerate existing runtime secrets. The app waits for migrations and
checks the native `/api/health` route. Its 3 GiB container memory limit has swap
disabled. The gateway remains a separate vault-backed host process.

The optional Python `dashboard` extra supplies Flask and OS-vault support.
The Docker-free [local helper](./local.py) uses a separately marked Windows source
cache and dedicated PostgreSQL database. Its earlier development attempt did not
complete browser acceptance after resource failures. It is not the accepted
runtime above; do not start another cold local compilation on this host.

To reproduce build inputs from a source checkout, [prepare.py](./prepare.py)
requires the clean pinned upstream source and verifies all overlay/adapter hashes.
`--build-only` exports public inputs without reading or creating private runtime
configuration. Provider keys never enter the build. [docker_build.py](./docker_build.py)
runs dependency/source preparation and the bounded compiler. Compilation refuses
engines below 11 GiB, reserves 2 GiB for the engine, and caps compiler RAM at
12 GiB. Swap allowance is at most 2 GiB, or 8 GiB on the disposable hosted CI
runner only. Commands have a 20-minute bound and 3 GiB host-storage floor.
Do not run full native TypeScript or compilation on this laptop.

[build.mjs](./build.mjs) launches Next from `apps/sim`, with an absolute CLI path,
an ephemeral build-only Better Auth secret and explicit preview routes. It uses
Webpack and experimental `compile` mode for request-time rendering. The separate
`generate-env` pass is excluded because its broad replacement regex corrupted
native configuration member access. The pinned compile-mode server supplies
static environment values at runtime. A small real Next fixture verified native
configuration access, model routes and API POST serving before the full build.

[build-traces.cjs](./build-traces.cjs) omits the final packaging dependency collector
only for pinned Next 16.4.1 and this full-source/dependency image. Its hash is
manifest-bound; runtime does not preload it. Standalone output remains refused.
[dev-next.config.mjs](./dev-next.config.mjs) retains native settings, uses pure
browser permission/password/logger helpers, keeps server crypto/auth intact,
externalizes server SDKs and disables persistent build caching/source maps.

The [completed CI build](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40393077051)
ran native contracts, owned TypeScript, compilation and 3,805 generated server
syntax checks separately. Each syntax check has a fresh bounded Node process.
Successful output was exported only after private-draft/anonymous-404 checks, with
original part hashes and range metadata. Local import verified the exact CI
configuration, manifest binding and filesystem identities. Its temporary private
release was deleted after browser acceptance. A local verified archive remains
under `D:/MinusOpsNativeSim/` for recovery. An earlier unintended public image
publication was removed; its download history is unknown. See acceptance.

Contract tests require this source checkout, the pinned upstream tree and installed
esbuild, Zod and Next dependencies. Defaults are
`artifacts/references/canvas-upstreams/sim` and `app/sim`; override with
`SIM_NATIVE_SOURCE_ROOT` and `SIM_NATIVE_DEPENDENCIES_ROOT` (`%USERPROFILE%/minus-sim`
for the existing Windows cache). They replace auth/DB and HTTP boundaries and
make no live model calls:

```powershell
node --test --test-reporter=tap app/sim-native/tests/overlay.test.mjs app/sim-native/tests/completion.test.mjs
```

The optional [type-check harness](./tests/typecheck.cjs) belongs on the bounded
CI host, separately from compilation. It checks owned files and reports unrelated
diagnostics; neither that scope nor the controlled browser checks establish full
application or production qualification. This loopback preview bypasses login.
Production authentication, hosting and licensing require separate qualification.
WSL previously wrote crash dumps despite the container core limit; it does not
establish crash-dump isolation. See the [file context](./CONTEXT-sim-native.md).
