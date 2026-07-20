# CEF Hiring-Coach QA — System Reference (living doc)

> **Purpose.** Single source of truth for the CEF/Cere QA-agent system: what it does, every tool, how it runs (CI + hosted server), which agent/version it targets (published vs pre-published), and where results land (Notion / Slack / Telegram). Keep this updated whenever a tool, flag, secret, endpoint, or pipeline step changes.
>
> **Owners:** Krishna (OpenSRE log-inspection + pipeline) · Bren (hiring-coach eval + quality bar). Platform/infra (inference nodes, allowlists, vault-api): Ulad / Sergey.
> **Last verified:** 2026-07-20.

---

## 1. What this system is

An automated QA agent for the **Manykind "hiring-coach"** CEF agent. For each new (or forced) manifest version it fires interview clips at the agent on the test cluster, then reports readiness across four **dimensions** into a Notion dashboard and an RCA to Slack/Telegram.

| Dimension | What it measures | How | Owner script(s) |
|---|---|---|---|
| **Functional** | Did a run execute end-to-end? (verdict + RCA) | eval fires 1 clip → OpenSRE investigates the run's vault logs | `manifest-watch.ts` → `lab-batch-run.ts` → `opensre investigate` → `notion-push.ts` |
| **Quality** | Score quality vs. expected ranges (16 clips) | eval fires 16 clips → per-clip pass/fail predicate | `quality-bar-run.ts` → `quality-push.ts` |
| **UX** | Does the deployed widget work? (T1/T2) | Midscene/Playwright vision tests of the widget | `midscene-qa/` → `build-ux-result.mjs` → `notion-push.ts` |
| **Deployment** | (placeholder) | — | not built |

**Two ways it runs:** (a) the **CI/CD pipeline** (`.github/workflows/hiring-coach-qa.yml`, twice-daily cron + manual) and (b) the **hosted OpenSRE server** (on-demand `/investigate`, sandbox testing). Both share the same OpenSRE core.

---

## 2. Environments & identifiers (CEF / Cere, test cluster)

All CEF work targets the **`dragon1-testnet`** cluster.

| Thing | Value |
|---|---|
| vault-api | `https://vault-api.compute.test.ddcdragon.com` |
| marketplace | `https://agent-marketplace.compute.test.ddcdragon.com` |
| GAR (agent registry) | `https://gar.compute.test.ddcdragon.com/api/v1` |
| DDC S3 gateway (audio clips) | `https://ddc-s3-gateway.compute.test.ddcdragon.com/hiringcoach-public/scenarios/audio/<stem>.<NNN>.mp3` |
| vault UI | `https://vault.compute.test.ddcdragon.com/` |
| Grafana | `https://dashboards.cere.io` |
| **DDC Dragon inference (OpenSRE's LLM)** | `https://inference-gateway.compute.dev.ddcdragon.com/api/v1/inference` — **dev** gateway (the test `orchestrator.…` had no `gemma4_31b` nodes; switched 2026-07-17). Env-overridable via `DDCDRAGON_BASE_URL`. Model `gemma4_31b`, bucket `1338`, version `v1.0.0`. |

**Current agent (post-migration / testnet reset ~2026-07-20):**

| Thing | Value |
|---|---|
| Agent-service pubkey (AS) | `34bba26b1b080fd381b4e84a4e74befd61653697dd0b610bf75ccbc3bd6c8760` |
| Agent id | `34bba26b…:hiring-coach-lab2` |
| Published version | `0.8.79` |
| onAudio event type | **`analyze.audio`** (base, **no suffix** — confirmed by Bren for v0.8.79) |
| QA vault id | `v-7e096cc01dc60b0a09c7cb0c386913cb` |
| QA vault owner wallet pubkey | `321ed25b93f4c372b4a20520468b43ccb44d2d4e9ffc5744515687b711f0688d` |
| QA wallet keystore | `6QeLWV6XLRYbxwwMgnv7PSHFJgYWYc38xFk6htS2fWkzcZ1R.json` (Polkadot ed25519 keystore; password held in CI secret `CEF_WALLET_PASSWORD`) |
| Cubby (analysis DB) alias | `hiring` (table `analysis_runs`) |

**Deprecated (no longer resolve after the reset):** eval AS `5df19be7…`, widget AS `148c4da8…`. `hiring-coach-qa` alias is **404 under the new AS** — only `hiring-coach-lab2` exists, so QA is lab2-only.

---

## 3. Published vs. pre-published (publishing ≠ installing)

Core concept (`manifest-watch.ts`): a **published** manifest sits in the marketplace, but a vault **AgentConnection pins a version** until re-connected. Publishing a new version does **not** upgrade a running connection.

- **Published version** = `sdk.marketplace.getAgent(agentId).version`.
- **Installed/connection version** = `vault.agents.get(agentId).version`.
- `manifest-watch` compares them; if the connection is behind (`needInstall`), it **disconnects + reconnects** to the published version (waits for `active@<version>`), then runs the eval.
- **We QA the version the connection is on.** In the normal (auto) path this is the latest *published* version, installed exactly once. There is no separate "pre-publish" (ROC-integrated, test-before-publish) path yet — that's a roadmap item (Fred's ROC pre-flight check).

Modes (flags):
| Flag | Effect |
|---|---|
| *(none)* | **Dry-run** — reads only, mutates nothing. |
| `--apply` | Perform the destructive disconnect+reconnect install, then eval. |
| `--no-state` | Stateless (CI): decide purely from connection-vs-published version; the connection version *is* the state → each version QA'd once. |
| `--force` | Re-QA even if the connection is already on the published version (still upgrades first if behind). Manual/on-demand + scheduled. |
| `--no-eval` | Install only, skip the eval kick. |

---

## 4. `qa-agent/` tooling (TypeScript, ESM, run via `tsx`)

Package `@cef-ai/qa-agent`. **Two CEF SDKs:** `@cef-ai/vault-sdk@2.0.0` (high-level `VaultSDK` — connections, `vault.scope().publish`, `vault.jobs`) and `@cef-ai/client-sdk@0.0.17` (low-level `Vault` — `events.publish`, `cubbies.query`). Wallet via `@polkadot/keyring` `^14`.

> **vault-sdk 2.0.0 breaking changes** (adapted 2026-07-20): `vault.agents.connect` takes `{ agentId, scope, settings }` (was `{ manifest, … }`); marketplace card dropped `manifestCid`; job-steps resource renamed `activities` → **`tasks`**.

| Script | Purpose | SDK | Runs |
|---|---|---|---|
| `manifest-watch.ts` | CI entrypoint (Functional). Detect/install new version, then spawn eval. Passes `--asr-alias` (`CEF_ASR_ALIAS`, default `parakeetTdtV2`). | vault-sdk | CI `qa` job (`--apply --no-state $FORCE_FLAG --clips HIA-C1`) |
| `lab-batch-run.ts` | The eval. Publishes `analyze.audio` (type from `CEF_AUDIO_EVENT_TYPE`), polls `analysis_runs` cubby, emits alerts (`QA_EMIT_FILE`) or POSTs to `OPENSRE_URL/investigate`. Rich `profile.*` overrides incl. `--asr-alias`. | client-sdk | spawned by manifest-watch; standalone |
| `quality-bar-run.ts` | 16-clip quality bar. Publishes `analyze.audio` + `profile.asrAlias`, polls cubby, emits one `row.json` (per-clip pass/fail vs expected ranges). | client-sdk | CI `quality` job (`--out quality-row.json`) |
| `quality-bar-inputs.ts` | Data module: the 16 clips (`clip_code`, `stem`, `chunks`, expected `authenticity/clarity/engagement`) + `checkExpected()`. | — | imported by quality-bar-run |
| `connections.ts` | **Ops tool.** List / connect / prune the QA vault's agent connections. Dry-run by default; `--connect <as:alias> --prune --apply`. | vault-sdk | manual/ops |
| `eval-simple.ts` | 1-clip smoke test + agent-log reader (jobs→tasks→logs). | vault-sdk | manual/smoke |
| `notion-push.ts` | Mirror one QA result into Notion (matrix cell + audit row + RCA body). Dimension-agnostic (`--dimension`). Inline image attachments. | @notionhq/client | CI (Functional, UX) |
| `quality-push.ts` | Push a quality `row.json` into Notion (Quality cells + delta + audit row). | @notionhq/client | CI `quality` job |
| `dashboard-toggle.ts` | Rebuild the per-agent "🕘 Last 10 runs" toggle + "🏔️ Best today" callout on each readiness page. | @notionhq/client | CI `dashboard` post-job |

**Standalone kit (not committed):** `eval-script/hiring-batch-runner/` — Bren's canonical batch runner (from `agent-catalog:feat/hiring-coach-dev-14`), client-sdk, `--asr-alias`, `--preset broad|hitlist`, writes CSVs. Used for manual eval sweeps.

**Event payload shape** (both eval scripts): `{ conversation_id, candidate_id, audio_ddc_urls: [...], profile: { asrAlias, authMode?, ... } }` → published to `target=<agentId>`, scope `default`.

**Cubby read** (client-sdk): `vault.cubbies.query(vaultId, 'default', agentId, 'hiring', { sql, params })` against `analysis_runs` (columns: `status`, `reading_likelihood` (auth), `clarity_overall`, `engagement_overall`, per-dim sub-scores, `timeline_json`, …).

---

## 5. OpenSRE CEF integration (Python, `app/`)

| Area | Detail |
|---|---|
| `app/services/cef/client.py` | `CefVaultClient` — signed (ed25519) reads. `cubby_query` → `/api/v1/vaults/{v}/scopes/{s}/agents/{a}/cubbies/{alias}/query`; `list_jobs` → `/vaults/{v}/agents/{a}/jobs`; `list_activities` → `/vaults/{v}/jobs/{j}/**tasks**`; `activity_logs` → `/vaults/{v}/jobs/{j}/tasks/{t}/logs`. (Keeps "activities" naming; hits the renamed `/tasks` endpoint — vault-sdk 2.0.0.) |
| `app/services/cef/qa.py` | `run_cef_qa(req)` — the hosted core. `CefQaRequest` (`context_id` + optional `cef`/`grafana`/`deliver_telegram` creds) → `_merge_with_env` → `run_investigation` → `CefQaResult` (`verdict`, `confidence`, `validity_score`, `root_cause`, `findings`, `report`, …). Same core used by CLI + microservice. |
| `app/services/cef/report.py` | Verdict gate (single source of truth): `cef_verdict` → `needs_review` if `validity_score < 0.5`, else `pass`/`no_go` from `_is_pass`. `_confidence`: high ≥0.8 / medium ≥0.5 / low <0.5. `format_cef_qa_telegram` renders the beautified report. `send_cef_qa_report` (Telegram). |
| `app/services/cef/wallet_signer.py` | Decodes Polkadot keystore → `WalletSigner`. `signer_from_material(wallet_json|wallet_path, password)` (prefers in-memory). |
| `app/tools/CefAgentLogsTool` | `cef_agent_logs` — the run's OWN jobs/tasks/ctx.log (resolves job by `context==conversation_id`; distinguishes "no job yet" vs "beyond lookup window"). |
| `app/tools/CefComponentLogsTool` | `cef_component_logs` — platform component logs from Grafana Loki (owns the LogQL/topology; namespaces `cef-system`/`ddc`). |
| `app/tools/CefClipHistoryTool` | `cef_clip_history` — prior scores from `analysis_runs` for regression baselines. |
| `app/tools/CEFGuidanceTool` | `get_cef_guidance` — the verdict *rulebook* (LLM-facing): decide from the run's OWN activities+cubby, never from ambient errors; absence of the run's logs → LOW confidence / NEEDS REVIEW (never PASS/NO-GO). |
| `app/webapp.py` | FastAPI microservice. `GET /health|/ok|/` (open); `POST /investigate` (bearer-gated → `run_cef_qa`). Auth: `OPENSRE_API_TOKEN` shared token (CEF testers) OR Clerk JWT. |
| verdict exposure | `app/cli/investigation/investigate.py` `--output` JSON includes `validity_score`; for CEF alerts also `verdict` (`cef_verdict`), `confidence`, `root_cause_category`. `opensre cef-qa` (`app/cli/commands/cef_qa.py`) is the same core from the CLI. |
| `app/utils/slack_delivery.py` | `send_slack_report` — thread reply (inbound Slack) OR, with no thread, env-fallback **top-level** `chat.postMessage` via `SLACK_BOT_TOKEN`/`SLACK_DEFAULT_CHANNEL` (added 2026-07-17 so scheduled/CLI QA posts to Slack). |

---

## 6. CI/CD pipeline (`.github/workflows/hiring-coach-qa.yml`)

> **The authoritative copy runs from the default branch `main`** (GitHub registers schedules/dispatch from the default branch). Every job **checks out `ref: feat/cef-qa-vault-client`** for the *code*. ⚠️ The branch's own copy of the workflow is **stale/divergent** and never runs — see §10.

**Triggers:** cron `0 6,18 * * *` (06:00 & 18:00 UTC, 2 samples/day) + `workflow_dispatch` input `force` (bool, default `true`). `FORCE_FLAG=--force` unless a manual dispatch sets `force=false`.

**Jobs (qa/ux/quality parallel; dashboard after):**

- **`qa` (Functional)** — matrix `[hiring-coach-lab2]`, timeout 60m. Node 24 (pnpm 10) + uv/Python 3.13. Writes wallet from `CEF_WALLET_B64` → `CEF_WALLET_PATH`. Runs `manifest-watch.ts --apply --no-state $FORCE_FLAG --clips HIA-C1`; then for each emitted alert: `opensre investigate --input-json <alert> --output qa-result.json` → `notion-push.ts --dimension Functional`. Delivers RCA to Telegram + Slack (OpenSRE) and Notion (push).
- **`ux`** — timeout 30m. Node 20 (pnpm 9). Midscene via OpenRouter (`qwen3-vl`). `npx playwright install --with-deps chrome` + xvfb; runs `e2e/hiring-coach.spec.ts` (T1/T2) headed; `build-ux-result.mjs` → `notion-push.ts --dimension UX` with inline screenshots.
- **`quality`** — timeout 45m. `CEF_AGENT_SERVICE_PUBKEY` hard-coded to `0x34bba26b…` (main). Runs `quality-bar-run.ts --out quality-row.json` → `quality-push.ts`.
- **`dashboard`** — `needs: [qa,ux,quality]`, `if: always()`. Runs `dashboard-toggle.ts` once (avoids racing the parallel pushes).

**Secrets referenced:** `CEF_WALLET_B64`, `CEF_WALLET_PASSWORD`, `CEF_VAULT_ID`, `CEF_AGENT_SERVICE_PUBKEY`, `GRAFANA_READ_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID`, `SLACK_BOT_TOKEN`, `SLACK_DEFAULT_CHANNEL`, `NOTION_TOKEN`, `NOTION_READINESS_DB`, `NOTION_AUDIT_DB`, `MIDSCENE_MODEL_API_KEY`. (`CEF_AGENT_ID` is composed as `<CEF_AGENT_SERVICE_PUBKEY>:<alias>`, not read directly.)

**No-false-verdict property:** the `qa` step uses `set -e`, so a run that dies *before* OpenSRE investigates (vault down, no inference nodes) never calls `notion-push` → shows as a red ✗ in Actions but writes **no** audit row. The dashboard only ever reflects genuine completed verdicts.

---

## 7. Hosted OpenSRE server (on-demand / sandbox)

`ssh root@178.18.148.197` (host `dev-instance`). Separate from CI; same OpenSRE core. **Not a git checkout — `/opt/opensre` is a static snapshot; update by `scp` + `systemctl restart opensre.service`.**

| Thing | Detail |
|---|---|
| Service | systemd `opensre.service` → `uv run uvicorn app.webapp:app --port 8080`, WorkingDir `/opt/opensre` |
| Public edge | `qa-caddy` (Caddy `:8090`, injects the bearer) ← `qa-tunnel` (cloudflared **quick tunnel**) |
| Public URL | `https://<random>.trycloudflare.com` — **ephemeral, changes on restart** (currently `wave-unlikely-post-mechanical`). For a stable URL, convert to a named tunnel. |
| Env | inline unit + `/etc/systemd/system/opensre.service.d/override.conf`: `OPENSRE_API_TOKEN`, `CEF_VAULT_BASE_URL`=testnet, `CEF_CLUSTER`, `GRAFANA_*`, `LLM_PROVIDER=ddcdragon`, `DDCDRAGON_BASE_URL`=dev gateway, `SLACK_BOT_TOKEN`, `SLACK_DEFAULT_CHANNEL=manykind-agents-qa`. No CEF vault_id/agent_id/wallet in env — pass in the request. |
| Wallet on box | `/opt/opensre/qa-wallet.json` (chmod 600) |

**Trigger** (posts RCA to Slack + returns `CefQaResult`):
```bash
curl -sS -X POST http://localhost:8080/investigate \
  -H "Authorization: Bearer $OPENSRE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"context_id":"<run/conversation id>","cef":{"vault_id":"v-7e096…","agent_id":"34bba26b…:hiring-coach-lab2","wallet_path":"/opt/opensre/qa-wallet.json","wallet_password":"<pw>"}}'
```
> ⚠️ Any `/investigate` on the box posts its RCA to `#manykind-agents-qa` (Slack env is set). Use a real `context_id`; don't fire junk probes at the channel.

---

## 8. Reporting

### Notion — "Manykind Agents — Readiness" dashboard
- **Readiness DB** (data source `7b1128f1-776f-477a-8b16-932e279829f6`) — one row per agent (`Agent` title, `Layer`=`Manykind Agents`). Columns: `Functional`/`Quality`/`UX`/`Deployment` (verdict selects); **day-windows** `Functional/Quality (1d/3d/7d)`; **run-windows** `Functional/UX/Quality (last 1/3/7/10)` (dot+uptime%, no x/y — Fred's reframe); `Confidence`, `Manifest Version`, `Last checked`, `Latest RCA`, `Audit Trail` relation.
- **Audit Trail DB** (data source `6e0713f3-c129-4910-9078-e2d29b9c5ca0`) — append-only, one row per run (deduped on `Conversation ID`+`Dimension`); RCA report + per-clip breakdown in the body; screenshots inline (UX).
- **Views:** day (`1 day`/`3 days`/`7 days`/`Extended`) + run-based (`Last 1 run`/`3`/`7`/`10 runs`). Per-agent page has the `🕘 Last 10 runs` toggle + `🏔️ Best today`.
- **Policy:** matrix cell always mirrors the LATEST verdict (dot = last run) + window uptime%; raw counts/scores live only in the audit rows / toggle.

### Slack — `#manykind-agents-qa` (workspace Cere Network, bot **rcabot**)
Functional RCA posts here (env-fallback top-level post). Wired via `SLACK_BOT_TOKEN`/`SLACK_DEFAULT_CHANNEL` (CI secrets + hosted-server env).

### Telegram
OpenSRE's original channel (CI secrets `TELEGRAM_BOT_TOKEN`/`TELEGRAM_DEFAULT_CHAT_ID`). Both Slack + Telegram fire in parallel.

---

## 9. ASR / model notes

The hiring-coach pipeline's first stage is **ASR** (speech-to-text); "alias" = a manifest nickname for a model. Models run on GPU inference nodes with **per-AS allowlisting**.
- **`parakeetTdtV2`** — verified deployed + working (Bren, 2026-07-20). **Default** (`CEF_ASR_ALIAS`). Long-form capable.
- `whisper_turbo` / `whisperLarge` — allowlist-gated; `whisper_turbo` caps at 30s chunks.
- `parakeetTdtV3` — unverified. `canary1bFlash` — **no nodes** for our AS → caused every run to fail at `chunk.process` (the bug that motivated pinning the alias).
- **Fresh post-migration ASes** (like `34bba26b…`) can hit `no available inference nodes` until platform allowlists them. Also affects the LLM judge `gemma4_31b`. Fix is platform-side (Ulad/Sergey).

---

## 10. Known issues / TODOs

- ⚠️ **Stale branch workflow copy:** `feat/cef-qa-vault-client`'s copy of `hiring-coach-qa.yml` diverges from `main` (both aliases in matrix, no `ref` pin, Slack commented out, old quality pubkey `5df19be7…`, **no `dashboard` job**). It **never runs** (main is authoritative) but is confusing — reconcile it to match `main`, or delete it from the branch.
- **Deployment dimension** — placeholder, not built.
- **Auto-trigger on deploy/merge** — roadmap: Slack `#code-review` listener / `dragon-ansible` post-deploy `repository_dispatch` → run QA + map run→PR. (See `dragon-ansible/.github/workflows/deploy.yml`.)
- **ROC pre-publish QA** — roadmap: QA a manifest *before* publish in the QA vault.
- **Named Cloudflare tunnel** — the hosted server's public URL is an ephemeral quick tunnel; make it stable if shared.
- **Platform allowlisting** for `34bba26b…` on ASR + `gemma4_31b` — with Ulad/Sergey.

## 11. Credentials to rotate (surfaced in chat/history)

`SLACK_BOT_TOKEN` (rcabot), `OPENSRE_API_TOKEN` + `GRAFANA_READ_TOKEN` (in the hosted systemd unit), `NOTION_TOKEN`. The QA wallet keystore lives in the repo root — treat as test-only.

---

## 12. History

- **~2026-07-16→20 testnet reset** wiped QA-vault connections + old agent-services; new AS `34bba26b…` @ v0.8.79. Recovery: re-connect via GAR, vault-sdk 0.5.1→2.0.0 + connect API, cef client `activities`→`tasks`, inference→dev gateway, ASR→`parakeetTdtV2`, new pubkey + lab2-only matrix. First fully-green run: `29752687593` (Functional PASS, Quality 11/16, UX pass, dashboard).
