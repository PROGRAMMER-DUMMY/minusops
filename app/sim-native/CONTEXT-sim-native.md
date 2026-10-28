# Native Sim overlay context

[README.md](./README.md) records the upstream pin, native UI ownership,
vault credential path, transport configuration and internal nonproduction limits.
[ACCEPTANCE.md](./ACCEPTANCE.md) separates passing contract/transport checks from
the native browser and real-provider acceptance.
[patch-manifest.json](./patch-manifest.json) lists exactly 28 overlay files with
SHA256 hashes, original upstream hashes where applicable, and the gateway API.
Paths are relative to the pinned source root; no upstream or EE tree is vendored.

The app files below live under `overlay/apps/sim/`. Their hashes remain identical to
the active artifact overlay; changes require updating the manifest explicitly.

| Overlay file | Interface and role |
| --- | --- |
| [layout.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/layout.tsx) | Publishes `openrouter-chat` from `MINUS_OPENROUTER_CHAT === 'true'`. |
| [feature-flags-provider.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/providers/feature-flags-provider.tsx) | Adds the optional native feature flag type. |
| [navigation.ts](./overlay/apps/sim/components/settings/navigation.ts) | `isSelfHostedOverrideEnabled(..., openRouterChat=false)` and `resolveWorkspaceNavigation(options)` admit only the flagged BYOK exception without granting enterprise features. |
| [workspace-section-access.ts](./overlay/apps/sim/lib/settings/application/workspace-section-access.ts) | Supplies the same flag to the native server-side settings section gate. |
| [settings-sidebar.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/w/components/sidebar/components/settings-sidebar/settings-sidebar.tsx) | Mirrors that exception in the native settings navigation. |
| [byok.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/settings/components/byok/byok.tsx) | Includes a separate OpenRouter Chat section only for the flagged workspace scope. |
| [openrouter-chat-settings.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/settings/components/byok/openrouter-chat-settings.tsx) | `OpenRouterChatSettings({workspaceId, canManage})` reuses native single-key BYOK callbacks and sanitized fixed errors. |
| [model-selector.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/home/components/user-input/components/model-selector.tsx) | `ModelSelector({plan})` selects the isolated local control under the flag; the hosted implementation remains intact. |
| [openrouter-model-selector.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/home/components/user-input/components/openrouter-model-selector.tsx) | `OpenRouterModelSelector()` searches raw catalog IDs/names and saves independent model/advertised effort preferences. Context uses a compact native chip, retaining the full run in its accessible description and title. Missing models have no silent fallback. |
| [model-setting-trigger.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/home/components/user-input/components/model-setting-trigger.tsx) | `ModelSettingTrigger` bounds model/effort labels and collapses to native icons in narrow composers. Tooltips and accessible descriptions retain complete values. |
| [openrouter-chat.ts contracts](./overlay/apps/sim/lib/api/contracts/openrouter-chat.ts) | Zod schemas and six native route contracts validate UUID scope, vault-only status, bounded account catalogs, key input and replacement preferences. |
| [server.ts](./overlay/apps/sim/lib/openrouter-chat/server.ts) | `assistantRoute(action, method)` wraps native session/workspace permissions, admin/same-origin writes, duplicate/size refusal, fixed gateway origin, no redirects and bounded replies. Errors never forward gateway diagnostics. |
| [status/route.ts](./overlay/apps/sim/app/api/workspaces/[id]/assistant/status/route.ts) | Exports GET for sanitized OpenRouter status. |
| [key/route.ts](./overlay/apps/sim/app/api/workspaces/[id]/assistant/key/route.ts) | Exports POST `{key}` and empty-body DELETE; successful replies normalize to `{success:true}`. |
| [models/route.ts](./overlay/apps/sim/app/api/workspaces/[id]/assistant/models/route.ts) | Exports GET for the current account-filtered catalog; missing credentials refuse. |
| [preferences/route.ts](./overlay/apps/sim/app/api/workspaces/[id]/assistant/preferences/route.ts) | Exports GET/POST `{model,effort,max_tokens}` with catalog/option revalidation before save. |
| [openrouter-chat.ts queries](./overlay/apps/sim/hooks/queries/openrouter-chat.ts) | `useOpenRouterChat(workspaceId, chatId?)` manages remote public state; direct key helpers keep credentials out of React Query mutation variables. |
| [store.ts](./overlay/apps/sim/stores/openrouter-chat/store.ts) | `useOpenRouterChatStore` holds workspace-scoped public preferences/catalogs only, without persistence or hosted selection state. |
| [complete.ts](./overlay/apps/sim/lib/mothership/request/handlers/complete.ts) | Preserves native completion accounting and maps flagged local error codes to fixed safe sentences. |
| [architecture/page.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/architecture/page.tsx) and [workspace.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/architecture/workspace.tsx) | Native workspace route with compact renderer cards, initial fit, persistent resizable Chat/Toolbar/Editor and bottom diff/evidence. Reuses native panel/terminal stores/hooks; source edits remain proposals. Whole-window browser acceptance passed; see ACCEPTANCE.md. |
| [architecture/chat.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/architecture/chat.tsx) | `ArchitectureChat` binds a native conversation to an explicit workspace/run and validated chat id. `Conversation` reuses native `useChat`, `MothershipChat`, composer/model/history and draft state. An explicit native chat is created before sending, preserving the architecture route. Malformed or differently scoped URL selections never attach. Two controlled turns verified source context, reload/history and unchanged route in the actual browser. |
| [sidebar.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/w/components/sidebar/sidebar.tsx) | Mounts the flag-controlled Architectures group alongside Workflows. |
| [architecture-list.tsx](./overlay/apps/sim/app/workspace/[workspaceId]/w/components/sidebar/components/architecture-list.tsx) | `ArchitectureList({workspaceId, collapsed})` reuses native sidebar disclosure, run chips and a collapsed rail menu. Admin selection opens registered runs; loading, unavailable, empty and failed selection states remain explicit. |
| [minusops-architecture.ts queries](./overlay/apps/sim/hooks/queries/minusops-architecture.ts) | `useArchitectureWorkspace(workspaceId, runId?)` and `useArchitectureAction` share validated per-run views between sidebar and canvas. Selection cancels stale reads, updates the scoped cache only after success and refreshes the Chat context status. Finite library actions, source proposals and sticky chat bindings; no publication or apply. |
| [minusops-architecture.ts](./overlay/apps/sim/lib/api/contracts/minusops-architecture.ts) | Validates bounded source/workspace projections and finite inspection/proposal operations. No publication/apply operation exists. |
| [architecture/route.ts](./overlay/apps/sim/app/api/workspaces/[id]/assistant/architecture/route.ts) | GET/POST reuse native session/workspace admission, admin/same-origin writes and the private local gateway. |

[Realtime index.ts](./overlay/apps/realtime/src/index.ts) preserves the pinned
server and changes its listener to loopback for the Docker-free preview.

[overlay.test.mjs](./tests/overlay.test.mjs) exercises proxy admission, credential
non-echo, fixed transport, bounds/duplicate refusal, catalog/option checks,
Docker public-origin matching, exact manifest hashes, source parsing, preserved
hosted selection and self-hosted BYOK visibility. It reads a separately obtained
pinned source tree and installed dependencies, and replaces auth/DB and HTTP
boundaries. Missing prerequisites fail rather than skip; no live provider calls.
The architecture conversation rendering check exercises actual React rendering
with native transport/query boundaries replaced. It refuses malformed chat ids,
wrong run/workspace scopes and implicit chat creation during render; a valid
selection reaches the native hook with its existing id.
The architecture query check exercises the real schemas and request options,
workspace-scoped cache keys, cancellation of stale reads, failed selection and
status refresh. Browser geometry, menus and sidebar navigation are recorded in
[ACCEPTANCE.md](./ACCEPTANCE.md).
[completion.test.mjs](./tests/completion.test.mjs) checks known/unknown local error
reasons, disabled-flag behavior and preserved success/accounting behavior.
[typecheck.cjs](./tests/typecheck.cjs) runs native TypeScript against owned files
in a disposable source/dependency image, reports unrelated diagnostics separately
and fails on timeout; it is not a full application acceptance check.
It now uses a ten-minute SIGKILL bound; CI separately limits it to four CPUs and
12 GiB RAM plus up to 8 GiB swap, with a 10 GiB Go soft limit and a workflow timeout.
The local transitive check was stopped after excessive memory use. A subsequent
remote scoped check completed for 18 owned files with zero owned/config or
unrelated diagnostics. This is separate from full application qualification.

The [host gateway](../../core/integrations/sim_assistant_gateway.py) owns vault
credentials and per-workspace model preferences; the
[local runtime](../../core/integrations/sim_chat_runtime.py) owns frozen turns,
history/replay and cancellation. Native HTTP proxies use a server-only local
transport secret, never a provider key as `COPILOT_API_KEY`. The native worker
connection is text-only, even if provider catalog metadata advertises tools.
No overlay modifies Chat sending, generated schemas or EE files.
The model contract accepts OpenRouter's optional leading `~` alias marker without
rewriting IDs; its length, traversal and account-membership checks remain enforced.
[NOTICE.md](./NOTICE.md) retains upstream attribution and identifies the modified
Apache files. The private build archive preserves upstream root license notices.
[browser-password.mjs](./browser-password.mjs) preserves the pinned generator's
alphabet/default length and uses the native CSPRNG utility. Its manifest hash is
checked before preparing build inputs. Only the browser build aliases the shared
encryption import to this pure helper and workspace auth to existing predicates;
Node builds retain server crypto, DB authorization and request-context storage.
The browser logger guard selects its existing no-op context implementation.
The [prepare script](./prepare.py) validates the clean pinned source and overlay
hashes, exports the ignored build context, and preserves private environment
credentials. `prepare(source, build_only=True)` exports only public build inputs
and returns before private settings or Compose preparation. Its flat import of
the existing `operations_models.local_path` needs no CLI bootstrap.
[compose.json](./compose.json) pins the original services;
[openrouter-compose.json](./openrouter-compose.json) adds the local 3010/3012
source preview using the same data volumes. [Dockerfile.deps](./Dockerfile.deps)
installs pinned workspace dependencies on the official image; [Dockerfile](./Dockerfile)
applies owned source overlays and compiles the application once before starting
Next in production mode for this internal preview. Type checking is separate. Database
migrations must complete before the app starts; its generated cache is writable
by the existing non-root runtime user. The app health probe requests `/api/health`
so a root redirect cannot hide a failed Next instrumentation hook.
[dev-next.config.mjs](./dev-next.config.mjs) keeps native server SDKs external,
disables source maps and persistent Turbopack caching, enables Turbopack minification
and disables scope hoisting. The retained Webpack fallback disables its own
minification and caching with bounded module parallelism and sequential workers.
These files contain references only,
never actual private environment values or provider keys.

[local.py](./local.py) prepares a marked, pinned Windows source cache, installs
native dependencies and runs migrations, realtime and Next without Docker.
Its environment allowlist excludes ambient provider/cloud credentials; runtime
database admission requires the dedicated loopback database. Child output redacts
private runtime values. `dev` prepares the native config and CLI runtime, binds to
loopback and never runs `tsc`. The full production build remains resource-blocked.
See [test_sim_native_local.py](../../tests/test_sim_native_local.py) for its small
boundary and launch checks. The helper uses a separately provisioned PostgreSQL
database; it does not replace or migrate the stopped Docker database.

[docker_build.py](./docker_build.py) runs `deps`, `source` and `compile` for the
pinned preview. `checked(command, label)` enforces a host storage floor and a
20-minute command deadline, terminating the owned compiler container on failure
or interruption. `compile_memory()` refuses engines below 11 GiB, reserves 2 GiB
of engine RAM and caps the compiler at 12 GiB. Compilation checks that actual RAM
limit plus at most 2 GiB swap (8 GiB only on a disposable GitHub-hosted runner),
four CPUs and its PID limit before starting.
Only successful, non-OOM output becomes the local
preview image. Logs/resource evidence stay in the ignored runtime directory;
provider credentials are never part of the build. It does not establish native
type-check, browser or production acceptance.

[build.mjs](./build.mjs) invokes the pinned Next compiler with the manifest's
`preview_build_paths` and Webpack from the native app directory so font and other
`process.cwd()` lookups resolve correctly. Docker and local runtime launchers use
that same directory and an absolute Next CLI path. It preserves nonzero child exits and reports
signals and cgroup memory events on failure. This internal preview includes native workspace pages,
all API routes (including hidden discovery paths), and the model directory;
unrelated marketing routes are omitted. The config redirects `/` to `/workspace`.
Its `compile` mode renders the internal preview on request; full pre-rendering
hit the existing time bound. The `generate-env` postprocessor is excluded because
its regex corrupted native configuration member access. Next's compile-mode
server supplies the static environment at runtime. A tiny pinned-Next fixture
proved native-style environment access, model pages and API POST serving;
it does not establish full Sim acceptance. The compiler fixture preserves exit
statuses and checks that native configuration references remain executable.
The native route-scope test resolves the actual Next patterns and checks complete
coverage of those original areas; it does not establish execution of every API.
The launcher gives only its compiler child an ephemeral Better Auth secret and
keeps its inherited heap budget. Runtime secrets are supplied separately from
the existing private settings.

The preview keeps the complete source and node_modules. Its pinned file tracer
ignores dependency inputs and final tracing includes are empty; standalone output
is refused to prevent treating those reduced traces as complete packaging data.
[build-traces.cjs](./build-traces.cjs) preloads only during compilation and
omits the final dependency-packaging collector, which exhausted the 12 GiB heap
after successful native compilation. It requires Next 16.4.1; another version
fails closed. Its hash is manifest-bound and preparation checks it. The real
Next runtime fixture still serves pages, known/unknown model paths and API POSTs
after that omission. Runtime startup does not load the adapter. These settings are
local-preview shortcuts, not production qualification or a portable standalone build.

The [CI workflow](../../.github/workflows/native-sim-preview.yml) prepares only public
inputs on an ephemeral hosted runner, checks contracts and owned types separately,
then runs the bounded compiler. A harmless draft-release preflight proves draft
state and anonymous 404 for metadata and assets before image bytes are uploaded.
Successful output goes to that private draft as hashed gzip parts, with the
source revision and Docker image identity. Only private export steps receive the
workflow token; they need repository contents write permission. There are no
provider keys or private workspace inputs. A prior successful native image was
unexpectedly public in GHCR; its exact package was deleted and fresh anonymous
access denied. Registry publishing is removed. The maintained mount uses `/sim-native` so both contract
harnesses select the source overlay tree. Contracts include failed/interrupted
compiler children and raw PDF/Monaco asset handling.
The additional browser/server fixture compiles actual native logger, random and
permission helpers, verifies browser Node imports are absent and server crypto
and request context remain available. All 32 checks pass in the bounded Docker
test setup; native browser checks complement that contract coverage.

Corrected source `a4449d8a825a782d6b4f2683ba526abf3244b1bb` passed CI40393077051,
including owned types18, compilation and all3,805 generated server syntax checks.
Verified private parts were cached on D: before importing. The local manifest
binds the exact CI configuration and matching filesystem identities; all19 overlays,
both adapters, compiler launcher and config matched inside the image. The private
release was deleted after acceptance. Controlled native browser checks verified
vault save/delete, model/effort selection, two Chat turns/reload, database-backed
canvas drag/reload and model search. A clean user workspace and the normal gateway
are ready. The checks made no live inference calls. [ACCEPTANCE.md](./ACCEPTANCE.md)
records image identities, source scope and production/real-provider limitations.

[export-code.mjs](./export-code.mjs) optionally exports changed compiled files
against a SHA256-bound private baseline inventory. `compare(current, baseline)`
requires identical Node/platform/dependency fingerprints, bounded canonical paths
and unique hashes/sizes. `exportCode` checks maintained overlay bytes and writes a
private delta archive plus the complete expected inventory. CI still performs its
full build and server syntax checks. Workflow inputs or temporary nonsecret
repository variables select the private baseline; absent selection keeps the normal
full image export. [export-code.test.mjs](./tests/export-code.test.mjs) checks exact
retention/change/removal and refusal of drift or malformed inventory. This is an
internal preview delivery path, not portable approval or production qualification.

Current architecture build `eb3d85a23b9644ba394ae2d5e427b90f2c48afb1` passed
CI40460705637:37 native contracts,23 owned TypeScript files with zero diagnostics,
and3,810 server syntax checks. The installed non-root image matched all7,250
compiled files,24 source overlays and the runtime/dependency fingerprint. Browser
acceptance checked real278-declaration source inspection, separate offline edit/
diff/download, drag/save/reload, missing/failed plan evidence and selected Chat
context/recovery. No live inference or actual source publication occurred.
That first image's full workflow-window adaptation was still pending. The next
build below accepts that interaction; agent tools and composition remain separate.

Current working-panel source `c161dfc8a63dad9d1a75f3d7d3a3e24fdee8208e` passed
CI40483168262 attempt2:38 contracts,24 owned TypeScript files with zero diagnostics,
full compilation and3,813 server syntax checks. The installed image matched all
7,254 compiled files,25 overlays and runtime/dependency fingerprints. Actual browser
acceptance checked two source-bound synthetic Chat turns without leaving the canvas,
history reload, editor/diff/download, tab/draft persistence, panel/evidence keyboard
resize, layout reload, initial fit, narrow-window controls and context recovery.
No real provider inference or source publication occurred. The normal gateway was
restored and the isolated synthetic key removed. Native agent tools, component
creation, requirements, CLI cutover and production qualification remain pending.

The workspace follow-up adds native library menus and per-run routes/cache keys. Server-side
chat ownership and immutable run binding prevent cross-tab substitution. Source retrieval
is bounded and question-specific; provider failures preserve only finite safe categories.
CI40550973622, exact compiled image verification and actual browser acceptance passed for this follow-up; see ACCEPTANCE.md.

The subsequent uninstalled editor source adds route-local
[canvas-model.ts](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/canvas-model.ts)
layout history and literal draft parsing,
[native-interactions.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/native-interactions.tsx)
native mode controls, scoped keyboard selection/focus and group-drag undo,
[fields.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/fields.tsx)
native typed configuration, and
[context-status.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/context-status.tsx)
collapsed citation/budget/provider-usage inspection. Field proposals use the persisted command
journal; AI proposals use native tool events and require explicit retention. Chat history is
filtered by owned run bindings. The maintained native block renderer adds `showErrorOutput`
with a default of true; Architecture disables the workflow error footer. The type-check harness
includes maintained package overlays. See the [implementation ledger](../../active/native-architecture-implementation-2026-10-28.md)
for checks and remaining tickets. None of this paragraph establishes installed acceptance.

[node-actions.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/node-actions.tsx)
uses native menus/dialogs for bounded declaration duplication/removal.
[composition-tools.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/composition-tools.tsx)
uses source-bound catalogs for storage insertion and typed module connection review. Candidate
graphs retain original card positions, expose source diffs and reload through the command journal.
[host-review.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/host-review.tsx)
separates queued/claimed/replied host status from observed publication and source refresh.
The renderer's optional `connectionsEnabled` keeps static edge anchors while disabling unsupported
editing handles; its native Workflow default is unchanged. Pending-source Chat integration,
complete clipboard/presence/lifecycle parity and installed qualification remain separate work.

Additional uninstalled leaves are
[search.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/search.tsx) for scoped
navigation and exact replacement previews,
[annotations.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/annotations.tsx)
for inert shared notes/display components/groups,
[presence.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/presence.tsx)
for expiring advisory cursors,
[requirements.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/requirements.tsx)
for fresh host intake, and
[evidence.tsx](./overlay/apps/sim/app/workspace/%5BworkspaceId%5D/architecture/evidence.tsx)
for qualified plan instances/actions and declared delivery/Helm/Argo sources. Clipboard payloads
contain references only; the host resolves private copy-time commands. Pending Chat bindings
now carry exact draft identity, validated against the trusted session actor. These source changes
still require the qualified native type-check/build and installed-browser acceptance.
