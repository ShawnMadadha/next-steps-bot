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
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")
    try:
        resp = httpx.post(url, json={"text": text}, timeout=15)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Slack request failed: {e}")
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text}")
