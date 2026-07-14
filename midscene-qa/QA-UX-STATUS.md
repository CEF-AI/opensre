# Hiring Coach Widget — UX QA: status & handoff

Living doc for the **UX dimension** of Manykind-agent QA (Midscene + Playwright browser tests of
the Hiring Coach widget). Feeds the Notion readiness dashboard via `notion-push --dimension UX`
later. Pick up from here in a fresh session.

_Last updated: 2026-07-15._

---

## 1. Goal
Automate UX verification of the **"Hiring Coach — Result"** widget — the widget end users see in the
Vault. Each test → pass/fail + screenshots, and (later) `notion-push --dimension UX --report-url …`
so UX results land next to Functional in the readiness matrix.

## 2. Target & access
- **Widget** = the deployed **Manykind – Hiring Assistant** agent (hiring-coach only, no Lab).
- **Agent URL:** `https://vault.compute.test.ddcdragon.com/agents/148c4da814d9a7e9f5ffe8fb934c29b8931bdc49b9503d21d9454c869c77d5ac%3Ahiring-coach-lab2`
- **Login:** `qaagent@cere.io` / OTP `555555` (Cere embedded wallet, iframe `wallet.stage.cere.io/authorize`).
- **Location:** `opensre/midscene-qa/` (Playwright + Midscene, system Chrome via `channel: 'chrome'`).

## 3. How the widget works (confirmed by exploration)
- **`qaagent`'s vault provisions FRESH on each login** ("Creating security vault") — wait for it to
  appear then clear before navigating (handled in `helpers.login`).
- Agent page shows a one-time **"Allow widgets to sign with your wallet?"** modal → must click
  **Allow** or the widget can't load data (handled in `helpers.openWidget`).
- The widget renders in a **sandboxed iframe** on the S3 gateway:
  `…/hiringcoach-public/widgets/<agentService>/hiring-coach-lab2/<version>/result.html`
  → target via `frameLocator('iframe[src*="hiringcoach-public/widgets"]')`. **Main-page DOM queries
  do NOT see widget content.**
- **Input view** ("Analyze an interview"): candidate-name field (`input[type=text]`,
  placeholder "e.g. Heinrich Vogel"), **Import recording** (hidden `input[type=file]` → use
  `setInputFiles`, no OS dialog), **Record meeting** ("Your mic + a Zoom/Meet tab").
- **Import flow:** select file → **"New upload · 0:12 captured"** card → "✧ Analyze interview"
  button → **"Uploading…"** → **"Analyzing…"** → result.
- **Record flow (from the video):** click Record → mic permission → **getDisplayMedia tab-share
  dialog** ("Choose what to share" + "Also share tab audio") → recording UI (waveform + timer +
  **"⏸ Stop"**) → Stop → uploads/analyses → result. Recording is saved as a **.wav** (downloadable).
- **Result view** (from the demo video, not yet reached live here): Clarity / Engagement scores,
  "Overview · Key moments", **Conversation timeline** with a **custom audio player** (no raw
  `<audio>`; timer like `0:00 / 0:25`, play/pause, threshold-colored bar), and **Flagged chunks**
  with transcript.

## 4. Test scenarios (from the walkthrough video)
| # | Scenario | Pass criteria | Status |
|---|---|---|---|
| **T1** | Open the widget | Input view renders (Analyze/Import/Record) | ✅ implemented + validated |
| **T2** | Import a clip → analysis starts | Reaches "Uploading/Analysing" (audio accepted) | ✅ implemented (validated to the analysing state) |
| **T2b** | Result completes within 5-min deadline | Result renders ≤5 min | ⛔ blocked (see §6) — `test.fixme` |
| **T3** | Playback: click Play → timeline advances | Timer/bar moves; audio request not 403 (guards 2-h temp-cred expiry) | ⛔ needs completed result — `test.fixme` |
| **T3b** | Seek: click timeline → jumps to section | Player time jumps | ⛔ needs completed result — `test.fixme` |
| **T4** | Record (mic) → stop → analysis starts | Recording UI → Stop → analysing | ⚠ implemented, **not validated live** (getDisplayMedia) |
| **T5** | Record (tab audio) → analysis starts | Same, input = a tab playing audio | ⛔ `test.fixme` (needs tab-audio source + auto-select) |
| **T4b** | Recorded result completes ≤5min, plays, downloads | | ⛔ blocked (see §6) — `test.fixme` |

Pass/fail gate for any record/import: **result must complete within 5 min** (configurable via
`QA_PROCESS_DEADLINE_MS`, default 300000) — "an 11s clip shouldn't take 5 min" (Bren).

## 5. Files
- `e2e/helpers.ts` — `login()` (native Cere wallet, no vision model), `openWidget()` (login →
  agent URL → Allow → returns the widget `Frame`), `AGENT_URL`, `WIDGET_IFRAME`.
- `e2e/hiring-coach.spec.ts` — T1, T2 (active) + T2b/T3/T3b (`test.fixme`).
- `e2e/hiring-coach-record.spec.ts` — T4 (active, unvalidated) + T5/T4b (`test.fixme`). Runs under
  the `chrome-media` project only.
- `e2e/explore-hiring-coach.spec.ts` — exploration scaffold (frames/DOM dumps). Reference only.
- `playwright.config.ts` — `chrome` project (default, ignores `*record*`) + `chrome-media` project
  (fake mic WAV + `--auto-accept-this-tab-capture`, runs only `*record.spec.ts`).
- `fixtures/` — `test-clip.{mp3,wav}` (97s, from `b0_henrich_clarity_fp.mp3`) and
  `test-clip-12s.{mp3,wav}` (12s trims). WAV = 16 kHz mono PCM (fake-mic input). `test-clip.wav` is
  the fake microphone (see config).

## 6. THE BLOCKER (why T2b/T3/T4b are deferred)
On `qaagent-vault`, a **12-second imported clip did NOT finish processing within 5 minutes**
(observed: stuck at "Uploading… → Analyzing…" through the 300s gate). This matches the tracker note
**"blocked on: NeMo models not live, 2 active workarounds (F0 diarization, Whisper throughput)."**
So either the backend is genuinely that slow now, or `qaagent`'s fresh vault isn't fully wired for
inference. Until a vault where processing completes is available (e.g. the `demo-100-vault` in the
video, which had 6–7 finished interviews), the completion/playback/seek tests can't be validated —
and would legitimately FAIL the 5-min gate against this vault (which is correct QA behavior).

## 7. How to run
```bash
cd opensre/midscene-qa
npm install                       # once
# .env: set MIDSCENE_MODEL_API_KEY (OpenRouter) only if you add Midscene ai() steps; T1/T2 are
# native Playwright (no vision model, free).
npx playwright test ./e2e/hiring-coach.spec.ts --headed --project=chrome            # T1, T2
npx playwright test ./e2e/hiring-coach-record.spec.ts --headed --project=chrome-media  # T4 (unvalidated)
npx playwright test ./e2e/hiring-coach.spec.ts --headed --project=chrome -g "T1"    # single test
```
Notes: `--headed` (the Cere wallet iframe + capture behave better headed). Cold first login is slow
(~2 min: fresh-vault provisioning). Screenshots land in `test-results/`.

## 8. Pending / next steps (in priority order)
1. **Unblock processing** — get a vault/account where a clip completes (or wait for NeMo/Whisper
   throughput fix). Then un-`fixme` T2b/T3/T3b/T4b and capture the **result-view player DOM** (play
   button, timer, timeline) to write real playback/seek selectors (currently only known from the
   video, not live DOM).
2. **Validate T4 live** — confirm `--auto-accept-this-tab-capture` actually bypasses the
   getDisplayMedia dialog in headed Chrome. If it doesn't: try `--auto-select-desktop-capture-source="<tab title>"`,
   or run under `xvfb` on a headed Linux CI, or drive the OS picker.
3. **T5 tab audio** — open a 2nd tab playing `fixtures/test-clip.mp3` (`<audio autoplay>`), select it
   via `--auto-select-desktop-capture-source`, assert the result reflects that audio.
4. **T3 expiry guard** — assert the audio network request isn't 403 (the 2-hour temp-credential
   issue Bren described); catch the broken-playback failure mode.
5. **Wire to Notion** — on pass/fail, call
   `opensre notion-push --dimension UX --report-url <playwright/midscene report> --agent hiring-coach-lab2`
   (the push CLI is dimension-agnostic; UX column + Report URL already exist in the dashboard).
6. **CI** — add a workflow (or extend hiring-coach-qa.yml) to run the UX suite; record tests likely
   need headed + xvfb.

## 8b. Test style — FULLY AI (decided) with caching
The widget flow is driven **fully by Midscene vision** (`ai`/`aiTap`/`aiInput`/`aiAssert`) — chosen
because the widget is versioned + not our DOM, so vision survives layout/structure changes, and every
step then shows in the Midscene report. Extend the fixture via `base.extend(PlaywrightAiFixture())`
from `@midscene/web/playwright` (it reasons over a screenshot, so it sees iframe content). Validated
live: a real `aiAssert` against the widget passes (Qwen3-VL via OpenRouter, key in `.env`).

- **Caching** (`MIDSCENE_CACHE`, set in `playwright.config.ts`): reuses the AI's element-location
  plan for ACTION steps (`aiTap`/`aiInput`) across runs → fast + free, re-plans only when the widget
  changes. **`aiAssert`/`aiQuery` are NOT cached** — they evaluate the live screen every run, so each
  assertion still costs one vision call. So repeat runs = cheap actions + per-run assertion cost.
- **Two native exceptions (deliberate):** (1) **login** — the Cere OTP wallet iframe is setup, not
  the widget under test, and flaky/expensive to drive by vision; (2) **file upload** — `setInputFiles`
  only (can't inject a file by vision).
- Benign setup warning "execution context destroyed" (mid-navigation style inject) — non-fatal; add a
  short `waitForTimeout` before the first ai call if it recurs.
- **Optional hybrid:** `e2e/smart.ts` (`smartClick`/`smartFill`/`smartVisible`) still exists — native
  locator first, Midscene vision on failure, logged. NOT used by the default fully-AI specs; available
  if a deterministic-first mode is wanted later.

## 8d. CI integration — status
- **CI login validated ✅** — `.github/workflows/ux-login-probe.yml` (manual) proved the Cere wallet
  login + widget render work on a headless ubuntu runner **under xvfb** (native probe
  `e2e/ci-login-probe.spec.ts`, no OpenRouter). Runner setup: `actions/checkout` (ref
  feat/cef-qa-vault-client) → `setup-node@20` → `npm install` (NOT `npm ci` — lockfile is gitignored)
  → `npx playwright install --with-deps chrome` → `apt-get install xvfb` → `xvfb-run -a npx playwright
  test … --headed`.
- **Secrets set:** `MIDSCENE_MODEL_API_KEY` (OpenRouter), `NOTION_TOKEN/NOTION_READINESS_DB/NOTION_AUDIT_DB`.
- **Next (the full UX job):** add a parallel `ux` job to `hiring-coach-qa.yml` (same cron + manual):
  run the UX suite under xvfb with the OpenRouter key → produce a `ux-result.json` (verdict + MD
  summary) → `opensre notion-push --dimension UX --agent hiring-coach-lab2 --result ux-result.json
  --report-url <CI run>` (updates the UX matrix cell + appends a UX audit-trail row; MD summary in the
  row body, HTML report linked via Report URL / uploaded artifact). Delete the probe workflow once
  the full job is green. The `ux` job runs T1/T2 today; add T4/T5 when the backend unblocks (§6).

## 8e. CI run status + the login-flakiness blocker (current)
The full `ux` job is wired and its **plumbing all works** on CI: setup (Node + Chrome + xvfb + npm +
pnpm), run the suite, `build-ux-result.mjs` → verdict + MD, `notion-push --dimension UX` (updates the
UX cell + appends a UX audit row), `--attach` uploads the zipped HTML report + `ux-summary.md` as
file blocks, artifact upload. Local: T1 + T2 green (fully-AI, reveal/viewport, manual agent).

**Blocker — flaky Cere login on CI (not viewport).** On CI, T1 passes (login flakes once, recovers
on the retry) but **T2 fails: `openWidget` → "widget frame never appeared"** on all 3 attempts — i.e.
the login/fresh-vault-provisioning failed, *before* any vision step. Root cause: **every test does
its own full login** (fresh Playwright context), and the Cere wallet + fresh-vault provisioning is
slow/flaky on CI; doing it repeatedly (per test × retries) compounds it.

**The real fix (next):** log in ONCE and reuse the session — Playwright **`storageState`** via a
global-setup/auth project: one login saves cookies+localStorage (the embedded-wallet keypair /
provisioned vault), every test loads that state and skips re-login. Standard Playwright auth pattern;
removes the repeated-login flakiness. Then re-validate T1/T2 on CI + confirm the HTML/MD attachments
land on the audit row (attach code committed but not yet validated on a green run).

Other options if storageState doesn't persist the wallet session: (a) run T1+T2 as one test (login
once); (b) harden `login()` + bump retries; (c) a longer provisioning wait.

## 9. Related
- Functional QA + Notion dashboard: `opensre/qa-agent/`, `.github/workflows/hiring-coach-qa.yml`,
  and the memory note `notion-qa-readiness-dashboard`.
- UX feeds the SAME dashboard (Audit Trail `Dimension=UX`, matrix `UX` column, `Report URL` field).
