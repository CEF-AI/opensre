#!/usr/bin/env python3
"""Scan #manykind-agents-qa for a "run QA on Stage" request and emit trigger outputs.

The Slack bot token can READ the channel (channels:history) and POST (chat:write) but our plan has
no outbound-HTTP Workflow-Builder step — so instead of Slack calling GitHub, GitHub polls Slack.
A human posts e.g. "New PRs deployed, run QA on Stage: <pr links>"; this script (run on a cron by
qa-slack-poller.yml) finds the newest such request that the bot hasn't already answered, parses the
env + PR links, replies in-thread to claim it, and writes GITHUB_OUTPUT so the workflow fires QA.

Trigger rule: message contains "qa" + an env word + >=1 PR link. Env stage/staging/testnet runs;
other envs are politely refused (only Stage is wired today). Dedup: skip messages the bot already
replied to (no reactions scope needed). Processes one request per run (newest first).

Env: SLACK_BOT_TOKEN (req), SLACK_CHANNEL_ID (def C0BGKCUBK97), SLACK_BOT_USER (def U0BHY8BRGQM),
QA_POLL_WINDOW_MIN (def 40), DRY_RUN (skip posting + claiming, just print).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
# `or` (not get's default) so an env set to "" by an unset GitHub secret falls back correctly.
# Default: #code-review (C0165G9DWH5). Override via SLACK_QA_CHANNEL_ID secret.
CHANNEL = os.environ.get("SLACK_CHANNEL_ID") or "C0165G9DWH5"
BOT_USER = os.environ.get("SLACK_BOT_USER") or "U0BHY8BRGQM"
WINDOW_MIN = int(os.environ.get("QA_POLL_WINDOW_MIN") or "40")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

PR_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+", re.I)
ENV_RE = re.compile(r"\b(stage|staging|testnet|prod|production|mainnet|dev|devnet)\b", re.I)
STAGE_ENVS = {"stage", "staging", "testnet"}


def slack(method: str, params: dict | None = None, body: dict | None = None) -> dict:
    url = f"https://slack.com/api/{method}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    else:
        url += "?" + urllib.parse.urlencode(params or {})
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310 - fixed slack.com host
        return json.loads(r.read().decode())


def is_request(text: str) -> bool:
    t = text.lower()
    return "qa" in t and ENV_RE.search(t) is not None


def bot_already_replied(ts: str) -> bool:
    res = slack("conversations.replies", {"channel": CHANNEL, "ts": ts, "limit": 50})
    if not res.get("ok"):
        return False
    return any(m.get("user") == BOT_USER for m in res.get("messages", [])[1:])


def post(ts: str, text: str) -> None:
    if DRY_RUN:
        print(f"[dry-run] would post in thread {ts}: {text}")
        return
    slack("chat.postMessage", body={"channel": CHANNEL, "thread_ts": ts, "text": text})


def emit(**kw: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    line = "\n".join(f"{k}={v}" for k, v in kw.items())
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def main() -> None:
    if not TOKEN:
        print("SLACK_BOT_TOKEN not set")
        emit(go="false")
        return
    oldest = time.time() - WINDOW_MIN * 60
    hist = slack("conversations.history", {"channel": CHANNEL, "oldest": f"{oldest:.6f}", "limit": 50})
    if not hist.get("ok"):
        print(f"conversations.history error: {hist.get('error')}")
        emit(go="false")
        return

    for m in hist.get("messages", []):  # newest first
        if m.get("user") == BOT_USER or m.get("bot_id"):
            continue
        text = m.get("text", "")
        if not is_request(text):
            continue
        prs = sorted(set(PR_RE.findall(text)))
        env = ENV_RE.search(text.lower()).group(1)  # is_request guarantees a match
        ts = m["ts"]
        if bot_already_replied(ts):
            continue  # already handled
        if not prs:
            post(ts, "🧪 I see a QA request but no PR links — include full `…/pull/<n>` URLs and repost.")
            continue
        if env not in STAGE_ENVS:
            post(ts, f"⚠️ QA only runs against *Stage* (testnet) right now — `{env}` isn't wired. Skipping.")
            continue
        # Valid, unprocessed Stage request → claim it and emit the trigger.
        bullets = "\n".join(f"• {p}" for p in prs)
        post(ts, f"🧪 Running hiring-coach QA on Stage for:\n{bullets}\nResults will post here when it finishes.")
        print(f"claimed request ts={ts} env={env} prs={len(prs)}")
        emit(go="true", prs=",".join(prs), thread_ts=ts, env=env, channel=CHANNEL)
        return

    print("no new QA request found")
    emit(go="false")


if __name__ == "__main__":
    main()
