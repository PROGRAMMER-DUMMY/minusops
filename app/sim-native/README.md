# Native Sim OpenRouter overlay

The source connection is implemented. The first native image built and imported,
but browser testing found malformed generated JavaScript from `generate-env`.
The corrected compile-only build passed focused contracts and a small runtime
fixture; the replacement native build and browser acceptance remain pending.
See [the acceptance record](./ACCEPTANCE.md).

Internal nonproduction integration of the native [Sim application at
`df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5`](https://github.com/simstudioai/sim/tree/df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5).
The [manifest](./patch-manifest.json) binds 19 Apache source overlay files and
the original hashes of modified upstream files. This directory contains neither
the upstream checkout nor enterprise source. Native UI components, colors, Chat
history and replay remain in the pinned application; this is not a custom shell.
The manifest also binds the small browser password adapter. Webpack uses native
pure permission predicates and the logger's intended browser fallback, while
server builds keep their original DB, request-context and encryption modules.

Enable with `MINUS_OPENROUTER_CHAT=true`. Open
`/workspace/{UUID}/settings/byok` and the native **OpenRouter Chat** key manager.
The flag permits that native BYOK tab on self-hosted deployments without enabling
enterprise features. Chat uses a separate searchable model control and only
advertised reasoning efforts; Fast mode is hidden.

The key travels transiently from the native form through the authenticated Next
proxy to the host gateway's OS credential vault. The vault namespace is
`MinusOps.ai_assistant.<SHA256 of canonical gateway workspace path>`, with
`openrouter` as the credential entry. Provider keys never enter Sim's database,
public preferences, browser persistence or the local worker transport secret.
This connection requires a vault key and does not use ambient provider keys.

Native `/api/workspaces/{UUID}/assistant/{status,key,models,preferences}` proxies
use only the fixed Docker host or `http://127.0.0.1:5180/workspaces/{UUID}/{action}` and the
server-only `MINUS_ASSISTANT_GATEWAY_TOKEN` header. Writes require workspace admin
and same origin. Set `NEXT_PUBLIC_APP_URL=http://localhost:3010` for the 3010
preview; the proxy validates the public origin across Docker port mapping.

Preferences are `{model: string|null, effort: string|null, max_tokens: 1..32768}`.
Models come from the credential-scoped gateway catalog with no public fallback.
The local worker freezes these independent preferences per turn. It supports
text conversation only: no native execution tools, workflow actions, source
writes, publication or deployment. Hosted model schemas and Chat send code are
unchanged. Safe local failure reasons are displayed without forwarding provider
diagnostics.

The Docker-free Windows preview uses [local.py](./local.py), Node 24, Bun 1.4.2,
and a separate PostgreSQL 17 database with pgvector. Its owned source cache is
`%USERPROFILE%/minus-sim`; the clean reference checkout is never edited.
The private `.minus/native-sim/local.env` must contain the dedicated database's
`DATABASE_URL`: role/database `minus_native_sim` at `127.0.0.1:6543`.
The helper refuses remote database targets and strips ambient cloud/provider keys.
Private transport/encryption configuration remains in `preview.env`. Neither file
contains the user's OpenRouter key. With those prerequisites prepared:

```powershell
python app/sim-native/local.py prepare
python app/sim-native/local.py install
python app/sim-native/local.py migrate
```

Keep these in separate terminals:

```powershell
python -m core.integrations.sim_assistant_gateway --workspace . --token-file .minus/native-sim/preview.env
python app/sim-native/local.py realtime
python app/sim-native/local.py dev
```

The development server binds to `127.0.0.1:3010` and compiles requested routes.
This path does not run the native TypeScript compiler. The attempted production
build exhausted its 8192 MB compiler heap. A separate TypeScript check was stopped
after its transitive native imports consumed about 7 GB; it did not pass.
Do not run these heavy checks alongside the preview on this host.
The development attempt also exhausted local storage and was stopped; its generated
cache was removed. The settings page rendered, but key entry and Chat browser
acceptance did not complete. These commands describe setup, not a currently ready
preview. Resolve build/runtime capacity before starting another cold compilation.
Redis is omitted for this single-instance preview using native process/PostgreSQL
fallbacks. This does not qualify multiple replicas or distributed runtime features.
The Windows database/uploads remain separate from the Docker preview.

Install the Python `dashboard` extra for Flask and OS-vault support. For the alternative Docker build, from this source
checkout, prepare the build with `python app/sim-native/prepare.py`.
It requires the clean pinned reference checkout, creates the ignored Docker build
context, and retains existing database/encryption/transport secrets. It never
stores provider keys in environment files. The archive contains the pinned
upstream application for local evaluation; it is excluded from our distribution.

```powershell
python app/sim-native/docker_build.py deps
python app/sim-native/docker_build.py source
python app/sim-native/docker_build.py compile
python -m core.integrations.sim_assistant_gateway --workspace . --token-file .minus/native-sim/preview.env
```

The [build launcher](./docker_build.py) monitors host storage and stops below 3 GiB
free or after 20 minutes per command. Compilation requires at least 11 GiB assigned
to Docker. It reserves 2 GiB of engine memory and caps its own RAM at 12 GiB, with
at most 2 GiB swap (8 GiB only on a disposable GitHub-hosted runner), four CPUs
and no provider credentials. Insufficient engine
memory refuses before container creation. It checks actual container limits before starting, then saves the
compiled output as the preview image. Logs and resource evidence go to
`artifacts/runtime/native-sim/build-logs/`. A failed compiler container is retained
for inspection; check its ownership label before removing it to retry.
The dependency image forcibly restores complete packages because the official
standalone image contains pruned runtime dependencies. Its download cache is
removed in the same layer.

[build.mjs](./build.mjs) compiles the manifest's explicit preview routes: the
native workspace, all API routes (including hidden discovery paths), and the
models directory. Unrelated marketing pages are outside this local build.
Build and runtime launch Next from `apps/sim` so native font and file lookups
resolve against the app directory.
The compiler child receives an ephemeral Better Auth secret, replaced by the
existing private secret at runtime. The compiler keeps its explicit 12 GiB
old-space limit. The build-only [trace adapter](./build-traces.cjs) omits packaging
dependency tracing for the complete source/dependency image and rejects other
Next versions. It is not loaded by the runtime. Standalone output stays refused.
The root URL redirects to `/workspace`; native workspace controls and styling
are retained. The route-scope test resolves patterns with the pinned Next builder
and checks every original route in those selected areas.

Keep the gateway running. In a second terminal:

```powershell
docker compose --project-name minusops-native-sim --env-file .minus/native-sim/preview.env -f artifacts/runtime/native-sim/compose.json -f artifacts/runtime/native-sim/openrouter-compose.json up -d simstudio_openrouter realtime_openrouter
```

Open `http://localhost:3010/workspace`. The native app uses the existing project
database/uploads. The source app waits for the pinned database migrations before
starting. The original 3000 app is not replaced by this command.
The [build config](./dev-next.config.mjs) retains native settings, keeps server SDKs
external and disables source maps and persistent Turbopack build caching. The
current build uses sequential Webpack workers without minification or module
concatenation and a 12288 MB V8 ceiling. It runs Next's experimental `compile`
mode so the preview renders on request. Native configuration member accesses
must not pass through `generate-env`: its broad replacement regex generated
invalid JavaScript. Next's compile-mode server populates static environment
values at runtime. A small pinned-Next fixture passed native-style configuration
access, model pages and an API POST with compile-only output;
native browser acceptance remains separate. See the [Next CLI reference](https://nextjs.org/docs/app/api-reference/cli/next).
The retained
Turbopack settings disable scope hoisting and persistent caching. Docker's combined
memory limit remains the enforced bound. Neither compiler has completed this
preview locally: earlier attempts exceeded memory, and the final larger local
Turbopack attempt was stopped at the host storage floor. A remote Turbopack build
also failed without producing an image, and the later full pre-rendering run hit
its 20-minute bound. Type checking is a separate
acceptance step, never parallel with compilation. An exported internal preview
does not establish completed type checks or production readiness.
The running app serves that compiled output with a 4096 MB V8 ceiling under a
3 GiB container RAM limit with swap disabled. This build/start path has not yet passed browser acceptance; earlier dev
compilation attempts exhausted memory and disk.
This local preview bypasses authentication
and binds only to loopback. Production authentication, licensing and hosting
qualification are separate work. Do not remove database volumes or regenerate
encryption keys when restarting.

WSL's crash-capture pipe wrote multi-gigabyte Node dumps despite the container's
zero core-file limit. Attributed task dumps were removed after recording metadata;
unrelated files were retained. Do not treat the core limit as WSL crash-dump
isolation or claim production host qualification from this local setup.

Local compilation is stopped. The temporary WSL memory override was removed;
Docker is back at its original approximately 8 GB engine allocation. Do not raise
host memory or restart a local build automatically.

The [native preview CI workflow](../../.github/workflows/native-sim-preview.yml)
was approved and pushed to the isolated `build/native-sim-openrouter` branch.
The [completed run](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40374089795)
passed contracts, the owned TypeScript check, compilation and private export.
It uses a standard public `ubuntu-24.04` runner, reclaims unused
SDK storage on that disposable runner, requires 30 GiB free, and uses
`prepare.py --build-only` without reading or generating private configuration.
Overlay contract tests and the owned TypeScript check run separately before
compilation. No provider or AWS credentials are supplied. Before building, CI
creates a private draft release with a harmless probe, checks authenticated draft
state, and requires anonymous release/asset requests to return 404. Successful
images are gzip-compressed, split below the release asset limit, and uploaded
only while that privacy check holds. Part hashes and image/source identities are
recorded. Public workflow logs contain build output, not image contents.

An earlier native image built successfully, but the registry publication was
public. That package was deleted and fresh anonymous access returned 403.
No user keys or run data were included; external download history is unknown.
Registry publishing is now removed from this workflow.

Download the successful run's draft assets using authenticated repository access,
verify every part and the exact source/image identity, then stream the compressed
parts to `docker load`. On a constrained host, checksum-verified ranges avoid
staging the complete archive; the existing 3 GiB free-space floor still applies
while Docker expands the image. The first import stopped at that floor; after
scoped cache cleanup the complete import and content identity checks passed.
Docker's containerd store reports a manifest ID where the CI store reported the
configuration ID; verify the manifest's configuration digest and filesystem layer
identities before accepting that difference. Browser testing then found the
generated-code fault above, so that app was stopped. CI now checks each generated
server JavaScript file in a separate bounded process before exporting the image.
Delete the private draft after
the local image is verified and use the existing Compose command. Runtime
settings and provider keys stay on the
local host. Browser key entry, model/effort selection, Chat streaming, reload and
follow-up history still need verification there. Keep this image private for
internal nonproduction evaluation; the upstream tree includes unchanged EE files.

This full-source/dependency image does not need a reduced dependency-file trace.
The pinned Webpack trace plugin ignores traced inputs, and final tracing includes
are cleared. Standalone output is explicitly refused. Restore normal dependency
tracing before changing the packaging strategy; the local preview is not a
standalone production artifact.

Run `node --test app/sim-native/tests/*.test.mjs` from the repository root.
Tests need the pinned upstream checkout (default
`artifacts/references/canvas-upstreams/sim`, override `SIM_NATIVE_SOURCE_ROOT`)
and installed esbuild, Zod and Next dependencies (default `app/sim`, override
`SIM_NATIVE_DEPENDENCIES_ROOT`, set to `%USERPROFILE%/minus-sim` for Windows). They inject auth/DB and HTTP boundaries; no live
provider credentials or calls are used. The optional
[native type-check harness](./tests/typecheck.cjs) runs inside a disposable native
dependency image after overlays are applied, mounting this directory at
`/overlay`; it has a ten-minute hard compiler bound and must also have a separate
container memory limit. CI gives it four CPUs, 12 GiB RAM plus up to 8 GiB swap
and a 10 GiB Go soft limit, separately from compilation. Do not run it on
this laptop. It invokes the pinned compiler directly to avoid orphaned
Windows child processes. Targeted tests and source hashes
do not establish full native compilation, browser acceptance or production
qualification. See the [file context](./CONTEXT-sim-native.md).
