# Deploy-triggered QA — feature doc

> Run the hiring-coach QA pipeline against a **Stage** deploy, on demand from Slack, and post the
> verdict back where the team already works. Living doc — update as features land.
> Related: [CEF-QA-SYSTEM.md](CEF-QA-SYSTEM.md) (the whole QA system), the QA channel is **#code-review**
> (`C0165G9DWH5`), bot **rcabot** (`SLACK_BOT_TOKEN`).

---

## Objective

When new code is deployed to **Stage** (the CEF test cluster / `dragon1-testnet`), a reviewer should be
able to **QA it in one step from Slack** and get a clear **verdict** (did the deploy break the
hiring-coach agent?) back in the code-review conversation — without leaving Slack, standing up a
server, or hand-running CI. Longer term, the QA should fire **automatically on deploy** and, on
failure, **point at the PR that most likely caused it**.

Guiding constraints (learned the hard way):
- **OpenSRE is request/response, not a daemon** — no long-running Slack listener service.
- **Slack Workflow Builder has no outbound-HTTP step on our plan** — Slack can't call GitHub directly.
- **GitHub `GITHUB_TOKEN` can't start a `workflow_dispatch`** (recursion guard) — avoid needing a PAT.

---

## E2E Flow (current)

```
Human posts in #code-review:   "New PRs deployed, run QA on Stage: <PR links>"
        │
qa-slack-poller.yml  (GitHub Actions cron */10 · reads Slack with rcabot token)
  1. conversations.history → find a "qa" + env + PR-links message it hasn't answered
  2. parse env (stage→run; prod/dev→refused) + PR links; dedup via the bot's own reply
  3. reply in-thread: "🧪 Running hiring-coach QA on Stage…"
  4. run QA via workflow_call  (token-free — a call isn't a dispatch)
        ▼
hiring-coach-qa.yml   → Functional (OpenSRE) · Quality (16-clip bar) · UX (Midscene)
        ▼
deploy_summary        → posts the WHOLE verdict as a reply in the same thread:
                        overall + Functional + Quality (real verdicts) + full Functional RCA + PRs
```

Trigger convention: message contains **`qa`** + an **env word** + **≥1 PR link**. Wording is free
(`@rcabot`, "run QA on Stage", "QA the testnet deploy", etc.). Only **Stage/testnet** runs today;
other envs are politely refused.

---

## Feature 0 — Current version (BUILT ✅)

Slack-triggered, human-initiated, fully serverless. **No PAT, no GitHub App, no Slack Workflow-Builder
HTTP step, no dragon-ansible change, no new Slack scopes** — just the existing bot token + cron +
`workflow_call`.

**Components**
| Piece | Location | Role |
|---|---|---|
| `qa-slack-poller.yml` | `.github/workflows/` (main) | cron */10 → reads #code-review, claims a request, runs QA |
| `qa_slack_poller.py` | `.github/scripts/` (main) | parse (qa+env+PR links), dedup via bot reply, emit outputs |
| `hiring-coach-qa.yml` | `.github/workflows/` (main) | QA pipeline; `workflow_call` entry (prs/component/thread/channel) |
| `qa_deploy_summary.py` | `.github/scripts/` (main) | assemble the whole verdict (Functional + Quality + full RCA) |
| `deploy_summary` job | in `hiring-coach-qa.yml` | download dimension result artifacts → post verdict in-thread |
| `pr-context.ts` | `qa-agent/` (branch) | resolve PR links → title/body/files (used by Feature 2) |

**Verdict scope:** **Functional + Quality** (overall = worst of the two) + the full OpenSRE RCA.
**UX is intentionally excluded** from the deploy verdict (flaky CI login); it still runs for the
dashboard/Notion. Re-add via the formatter's `--ux` flag when it stabilises.

**Correctness note:** the summary reads each dimension's **result artifact** for its real verdict
(`qa-result.json` verdict+RCA, `quality-row.json` pass_ratio) — *not* job success/failure (the jobs
are soft: UX `set +e`, quality exits 0, a `no_go` is a successful investigation), so it can't show
green on a real failure.

**How to trigger:** post in #code-review, e.g. `New PRs deployed, run QA on Stage: <pr links>`.
Picked up within ≤10 min by the cron (GitHub schedules lag; a maintainer can also dispatch
`qa-slack-poller.yml` to run it instantly).

**Status:** validated end-to-end — message → claim → QA → whole-verdict reply in-thread.

---

## Feature 1 — Auto-trigger on deploy (from dragon-ansible) — NOT BUILT

**Goal:** drop the human step — when a Stage deploy succeeds in **`cere-io/dragon-ansible`**
(`deploy.yml`), QA fires automatically (or the bot auto-asks "these PRs deployed, run QA?").

**How it would work:** add a final step to `dragon-ansible/deploy.yml` (testnet only) that, on
success, triggers our QA — either a `repository_dispatch` to `CEF-AI/opensre` or a Slack post that
the poller picks up — carrying the deployed component/version.

**Blockers / open issues:**
- **Cross-org + their repo.** `dragon-ansible` is in `cere-io`; the step + any token/secret must be
  added there and **merged by a dragon-ansible admin** (not us). A `repository_dispatch` to
  `CEF-AI/opensre` needs a token with Actions/Contents write on our repo stored as a `cere-io` secret.
- **Deploys are per-component artifacts, not source.** `deploy.yml` is `workflow_dispatch` per
  `action` (deploy-core, deploy-inference-gateway, …) and ships prebuilt images — it **doesn't know
  which PRs** went in. So "which PRs deployed" still needs a source → deploy mapping, or the human
  supplies them (as today). Version pins do live in dragon-ansible PRs (`inventory/…/all.yml`).
- **Which deploys should trigger QA** (allowlist of hiring-coach-relevant components) to avoid a QA
  run on every component push.

**Interim:** Feature 0 (human posts "run QA on Stage …") covers this without touching dragon-ansible.

---

## Feature 2 — Analyse PR descriptions (failure → likely culprit PR) — PARTIALLY BUILT, not wired

**Goal:** when QA **fails**, correlate the RCA against the deployed PRs and post the **most likely
culprit PR(s)** with reasoning — so a regression is traced to a change, not just reported.

**Built:** `opensre correlate` (`app/services/cef/correlate.py` + `app/cli/commands/correlate.py`,
docs `docs/cef-qa-deploy.mdx`, tests `tests/services/test_cef_correlate.py`). Given an RCA + the PR
set (title/body/changed-files from `pr-context.ts`), the agent LLM ranks suspects; **conservative** —
an infra/upstream RCA (e.g. missing inference nodes) returns *no* suspects. Verified live.

**Not wired into the deploy flow** — by decision, v1 just posts the PRs (no correlation).

**Blockers / open issues:**
- **Cross-repo PR read token.** Fetching PR descriptions/diffs across `cere-io` / `Cerebellum-Network`
  needs a **`QA_PR_READ_TOKEN`** secret (fine-grained PAT: Pull-requests + Contents read on those
  orgs). CI's default `GITHUB_TOKEN` only reads `CEF-AI/opensre`. Without it, only raw PR *links* can
  be shown (which is what v1 does).
- **Disabled by choice** for v1 ("just post PRs, don't guess the cause"). Enable by wiring a
  `correlate` step into `deploy_summary`'s failure path once the token exists.

---

## Follow-ups / decisions
- Provision `QA_PR_READ_TOKEN` to unlock Feature 2 (correlation + PR descriptions in the summary).
- Decide whether to pursue Feature 1 (dragon-ansible auto-trigger) or keep human-initiated.
- Rotate `SLACK_BOT_TOKEN` (surfaced in chat during setup).
- UX back into the deploy verdict once its CI login is stabilised (session reuse).
