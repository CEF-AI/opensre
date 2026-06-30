# DAC Pipeline Health Check — Proof of Concept

## The goal
We want to know — **proactively, without waiting for something to break** — whether
the DAC pipeline is healthy from end to end. The pipeline has four stages that hand
off to each other:

**Collection → Aggregation → Inspection → Payout**

The idea: on a schedule, an assistant looks at how each stage is doing, decides if
everything is fine, and posts a short update to the team's chat — a **green "all
healthy"** note when things are good, or a **plain-language root-cause write-up**
when something looks off. No dashboards to babysit; the update comes to you.

## What "healthy" means (what we actually check)
The assistant looks at five everyday questions about the pipeline:

1. **Is new work still coming in?** — are activity records still being produced, or
   has the flow dried up?
2. **Is the minute-by-minute processing keeping up?** — is each minute's work
   finishing comfortably in time, or starting to lag?
3. **Are the period reports being produced on time?** — does each era close out and
   publish its summary when it should?
4. **Are enough validators checking each period?** — are the expected number of
   independent checkers participating?
5. **Are payouts moving?** — is settlement progressing so periods actually get paid?

Each question gets a simple **green / amber / red** grade, and those roll up into one
overall verdict.

## How it's set up
- It reads the live numbers and logs from **Cere's existing monitoring** (Grafana) —
  nothing new to instrument; we reuse what's already there.
- An **AI assistant** reads those signals, interprets them using built-in knowledge
  of how DAC works, and writes the update.
- For this proof of concept we ran the assistant **entirely on a local machine**
  (a laptop-class model), so no data or cost left the box.

## What we added
Two new capabilities, plus one behind-the-scenes reliability fix:

- **A DAC health checker.** Given a target network (testnet/mainnet/devnet), it
  gathers the five signals above and returns the graded verdict. This is the single,
  trustworthy "is it healthy?" answer.
- **A DAC knowledge pack.** A concise briefing on how the pipeline works — the
  stages, the timing expectations, and the common failure patterns — so the AI
  interprets the numbers correctly instead of guessing.
- **A reliability fix** so the assistant's requests for data don't fail on small,
  avoidable mistakes — it now recovers cleanly and retries instead of getting stuck.

## A note on accuracy (a false alarm we fixed)
One of the five checks — "are period reports being produced?" — first relied on a
raw internal counter, and it briefly raised a **false alarm**: it flagged a problem
when in reality the only period without a finished report was simply the **current,
still-open one** (which isn't supposed to have a report yet). We corrected this:
that counter is now shown for context but **no longer drives the verdict**. "Are
periods completing" is judged by the more reliable downstream signals (validators
checking + payouts moving) — if periods truly stopped, those would go quiet. Net:
no more false alarms from a still-open period, while a genuine stall is still caught.

## What happened (results)
- Run against the **live testnet, everything came back green**: work is flowing,
  processing is fast, period reports are on time, the expected validators are
  participating, and payouts are moving.
- The assistant **correctly concluded the pipeline was healthy** and explained why,
  with good confidence.
- It now **posts the result to Telegram** automatically — a test message and the
  health report both landed in the chat.

## What still needs improvement
1. **The AI model.** The local model we used works and reaches the right answer, but
   it's **slow** (around ten minutes per run) and occasionally sloppy about how it
   asks for data. A faster, stronger model — or a hosted one for production — would
   make it quicker and more reliable. (This is a model choice, not a limitation of
   the approach.)
2. **How the health checker gets its data.** Today the health checker fetches the
   signals itself rather than reusing the assistant's general-purpose data tools —
   necessary for now, but worth revisiting so the logic lives in one shared place.

## How you run it, and what you get
- **Quick check:** ask for the health verdict directly and get back a short summary
  with each of the five signals graded green/amber/red. Takes seconds.
- **Full report:** run the assistant on a "check DAC health" request. It gathers the
  signals, reasons over them, and **posts the report to your team chat (Telegram)** —
  green if healthy, or a root-cause write-up with suggested next steps if not.
- **On a schedule:** have this run automatically (e.g. every 15 minutes or once per
  period) so the team gets a steady, proactive heartbeat instead of finding out late.
