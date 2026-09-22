import logging
import os

import httpx

log = logging.getLogger(__name__)


def send_followup(commitment):
    """Post the follow-up draft to Slack. DRY_RUN=1 prints it instead."""
    due = commitment["due"] or ""
    due = f", due {due}" if due and due.lower() not in commitment["action"].lower() else ""
    text = f"Follow-up from {commitment['owner']}: {commitment['action']}{due}\n{commitment['followup_draft']}"
    if os.environ.get("DRY_RUN") == "1":
        print(f"[dry run] would post to Slack:\n{text}")
        return
    # Two ways in because Slack hands out two kinds of credential: the CLI gives a bot token, which posts to a
    # channel through the Web API, and the app settings page gives an incoming webhook URL tied to one channel.
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    try:
        if token:
            resp = httpx.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {token}"},
                              json={"channel": os.environ.get("SLACK_CHANNEL", ""), "text": text}, timeout=15)
        else:
            url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
            if not url:
                raise RuntimeError("neither SLACK_BOT_TOKEN nor SLACK_WEBHOOK_URL is set")
            resp = httpx.post(url, json={"text": text}, timeout=15)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Slack request failed: {type(e).__name__}")
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text}")
    if token and not resp.json().get("ok"):
        raise RuntimeError(f"Slack returned {resp.json().get('error')}")
