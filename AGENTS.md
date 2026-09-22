# Working on this repo with a coding agent

Two rules before anything else:

1. Read NOTES.md first. It lists what was confusing, missing, or surprising about the Recall API and docs while building this. Add a line whenever something new surprises you.
2. Never invent a Recall field. If a payload or response is not in the docs, log the raw body, then read the docs. Every docs page has a markdown version at the same URL plus `.md`.

Recall references:

- Docs index: https://docs.recall.ai/llms.txt
- Recall MCP server (docs search and API access from your editor): https://docs.recall.ai/docs/docs-mcp
- Pages this app was built from: bot-real-time-transcription, real-time-webhook-endpoints, real-time-event-payloads, bot-status-change-events, recording-webhooks, authenticating-requests-from-recallai, async-transcription, download-schemas, sub-codes (all under https://docs.recall.ai/docs/)

## Layout

Every file has one job. Keep it that way and keep files under 500 lines.

- `app/main.py` routes and page rendering
- `app/recall.py` Recall client: create_bot, get_bot, download
- `app/realtime.py` `/rt` handling: token check, dedupe, cue gate, windowing, fuzzy match
- `app/webhooks.py` dashboard webhook: signature check, dedupe, status changes, post-meeting pass
- `app/detector.py` model call, JSON parsing, dry run stub, the cue word list
- `app/prompt.md` the prompt (the block marked SHAWN WRITES THIS is human owned)
- `app/actions.py` sends a follow-up to Slack
- `app/store.py` sqlite schema and queries, nothing else
- `app/subcodes.py` status codes and sub codes to plain English
- `scripts/make_fixture.py` saves a real call's transcript as `fixtures/call.json`
- `scripts/replay.py` replays the fixture through `/rt`, optionally with the end of call webhooks

## Loop

```bash
.venv/bin/python -m pytest
DRY_RUN=1 .venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python scripts/replay.py --speed 4 --finish
```

Plain Python, plain HTML. No JS framework, no ORM, no base classes, no plugin system, no Docker, no CI. Comment only where a choice is not obvious, and make it one line saying why.
