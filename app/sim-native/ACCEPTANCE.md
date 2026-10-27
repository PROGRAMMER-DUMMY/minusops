# Native Sim OpenRouter acceptance, 2026-10-27

The native Sim application is running at `http://localhost:3010/workspace`.
Controlled browser acceptance passed. Live OpenRouter inference requires the
user's key and remains unverified by these checks.

Follow-up on 2026-10-27: the real credential-scoped catalog and native picker now
pass with 449 models, including 19 aliases beginning with `~`. The former identifier
regex rejected the whole catalog on those valid IDs. Backend catalog/preferences,
the native Zod contract and the Chat runtime now preserve the optional prefix;
length, traversal and account-membership checks remain enforced. All 174 focused
Python checks and 33 native contracts passed. No inference request was sent.

The running local image includes a bounded follow-up patch over the CI image below:
`sha256:da916ab9c6ec8aed1c65679edaeb589b1c94f7d9d9e9b9c400d5ba09900481ba`.
Five exact compiled schema chunks were hash-checked before changing the regex,
then syntax-checked; the maintained native source/manifest were updated as well.
This avoids another full build on the storage-constrained preview host. The prior
image is retained as `df3db64-before-model-alias-fix`; a full native rebuild of
this follow-up remains outstanding. Existing browser caches may need a hard refresh.
The live-catalog receipt is `model-alias-live-catalog-verification.json` in the
local evidence directory below. This is separate from live inference acceptance.

The application is pinned to Sim `df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5`.
The corrected build source is `a4449d8a825a782d6b4f2683ba526abf3244b1bb`;
[CI 40393077051](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40393077051)
passed 32 contracts, the scoped TypeScript check for 18 owned files with zero
owned/config or unrelated diagnostics, compilation, and syntax checks for all
3,805 generated server JavaScript files. This is not a full native application
type-check or production qualification.

The imported CI configuration digest is
`sha256:b7372f21afb0d7384cad572421eb1a214251a1453704661b7f07cc2d39beaa83`.
Docker Desktop's local OCI manifest is
`sha256:304164e059d2a8a1842895692c10ad8fe48d5f02976b5591b16eac39da464236`.
All three archive part hashes passed. The manifest and configuration bytes were
hashed, their binding was checked, and filesystem identities matched. All 19
overlay files, both adapters, the compiler launcher and native build config
matched their maintained bytes inside the image: 23 files checked.

The actual native browser checks established:

- OpenRouter key entry through its own BYOK form into an isolated Windows vault
  profile, without the key in localStorage or sessionStorage; deletion through
  that same form and confirmed vault cleanup.
- Selection of the controlled account catalog model and advertised `high`
  reasoning effort through the native controls.
- Two streamed Chat turns, history after reload and completed native UI state.
  The controlled worker received message counts one and three. Its responses
  explicitly identified the test; no paid inference was made.
- A native workflow node drag reached normalized database state and survived
  reload. The workflow was not executed.
- The native model directory rendered, and search updated its URL.
- A separate clean MinusOps workspace was created with no provider keys. Its
  normal gateway and private bridge were verified after fixture restoration.
  No uncaught page errors occurred in the layout/model-directory checks.

The preview health check now requests `/api/health`, rather than accepting a
redirect from `/`. The initial browser test's unscoped selector added a synthetic
OpenAI key only in its new test workspace. That exact entry was removed after
matching its provider, mask and creation time; tests now require the OpenRouter
section and modal before entering any value. Existing user workspaces were not
modified by that cleanup.

The preceding build's `generate-env` pass corrupted native configuration member
access. Compile-only output passed a small real Next runtime fixture and the
full native checks above. The version-checked build trace adapter omits packaging
dependency tracing for this full-source image; runtime does not load it and
standalone output stays refused. Restore normal packaging before production use.

The completed image was exported through a private draft with anonymous access
denied. A truncated range transfer was refused; verified full parts were then
cached on D: before importing without network reads. The temporary private
release was deleted after acceptance. Original database/uploads and runtime
encryption configuration were retained. Evidence and screenshots are local,
ignored outputs under `artifacts/reviews/sim-native-20261026/openrouter/`.

An earlier image publication was unexpectedly public. Its exact package was
deleted by the [quarantine workflow](https://github.com/PROGRAMMER-DUMMY/minusops/actions/runs/40372689852),
and subsequent anonymous access was denied. No user keys or run data were
included; external download history remains UNKNOWN. Registry publishing was
removed from the build workflow.

This connection supports text Chat. Architecture editing, agent workflow actions
and infrastructure operator integration remain separate work. Authentication is
bypassed only for this loopback preview; production authentication, hosting,
licensing and live-provider acceptance are not established. See [setup](./README.md).
