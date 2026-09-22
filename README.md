# Next steps bot

A Recall.ai sample app. A bot joins a sales call, commitments show up on a page while people are still talking, and after the call a second pass over the full transcript confirms them and drafts one follow-up each.

## What this is

The rep never sees this app. They see their CRM. This is the part a company like HubSpot would build on top of Recall to get next steps out of a sales call and into the deal record. Recall provides the bot and the live transcript. This repo is everything after that: webhook handling, dedupe, a cheap filter before the model, the model call, a second pass after the call for accuracy, and one action at the end. A customer keeps the pipeline and swaps the page for their own UI and the Slack post for their own CRM write.

## Why I built this one

At Chronos I built the send confidence engine for our outreach agents. Its whole job was deciding whether an action was safe to fire on its own or needed a human first. That's the same problem here. Pulling promises out of a live call with a model is the easy half. Deciding which of those promises the app is allowed to act on without a person looking is the half I wanted to show.

I also picked it because commitments are the most common thing built on Recall. Every conversation intelligence tool has a next steps feature. The existing Recall action items sample runs after the meeting. This one runs during the call and checks the live results against a full pass at the end, because an eight utterance window misses things, and a rep shouldn't have to wait until the call is over to see what they just promised.

## Run it in five minutes

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
```

1. Fill in `.env`: Recall API key, workspace verification secret, Anthropic key, a random `RT_TOKEN`, and your public URL. A static ngrok domain works: `ngrok http 8000`. For Slack, either a bot token in `SLACK_BOT_TOKEN` with the channel id in `SLACK_CHANNEL`, or an incoming webhook URL in `SLACK_WEBHOOK_URL`.
2. In the Recall dashboard, add a webhook endpoint at `PUBLIC_URL/webhooks/recall` subscribed to the `bot.*` status events plus `recording.done` and `transcript.done`. Real-time transcript events need no dashboard setup; the create bot call points them at `PUBLIC_URL/rt/`.
3. Start it:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

4. Paste a Google Meet link at http://localhost:8000, join the meeting yourself, admit the bot, and say "I'll send the order form by Thursday." Watch `/bots/<id>`.

No keys yet? Set `DRY_RUN=1` and use the replay below. The model is replaced by a regex stub and Slack messages print to the console.

## How it works

Bot. `POST /bots` creates a Recall bot with `recallai_streaming` transcription in low latency mode and one real-time webhook endpoint for `transcript.data`, pointed at this app with a token in the URL.

Live events. Every utterance lands on `/rt`. It's deduped on (bot, participant, start time, text) because Recall retries, then saved. A word list decides whether the utterance might contain a commitment. Only then does the detector run, over the last eight utterances. Anything it returns is fuzzy matched against what's already stored so the same promise isn't added twice. At or above the confidence threshold it shows as proposed, between 0.5 and the threshold as unsure, below 0.5 it's dropped. Recall gets a 200 before any of that runs.

Detector. One function, `detect(utterances, mode)`, sends `app/prompt.md` plus the transcript to the model and parses JSON back. Malformed output becomes an empty list and a log line, never a failed webhook.

Post-meeting pass. `recording.done` arrives on the dashboard webhook, signature checked, deduped by webhook id. The app downloads the full transcript, runs the detector once over the whole thing, and matches the results against the live commitments. A live commitment the full pass also found becomes confirmed. One it didn't find becomes superseded, because the person probably revised it or the live window misheard it. New ones the live pass missed are added as post_meeting.

Follow-ups. Each confirmed or post-meeting commitment gets a two line draft from the owner's point of view. Confirmed ones at or above the threshold are posted to Slack on their own and marked sent. Unsure and post-meeting ones wait on the page with approve and dismiss buttons.

## Replay a recorded call

```bash
.venv/bin/python scripts/make_fixture.py <bot_id>
.venv/bin/python scripts/replay.py --speed 4 --finish
```

The first command saves a finished bot's transcript as `fixtures/call.json`. Recall's download format is the same shape as the live event payload, so the second command can play it back through `/rt` at real pacing (`--speed 4` is four times faster) and, with `--finish`, send the end of call webhooks so the post-meeting pass and follow-ups run too. This is how I demo it without a live meeting.

## Decisions I made

The bot never speaks in the meeting. My first design posted each commitment into the meeting chat and it read like a stenographer clearing its throat. Live results go to the rep's screen instead, and the rep confirms with the customer out loud, which is what a good rep does anyway.

A word list gates the model. Most utterances aren't commitments and the model is the expensive step. The post-meeting pass catches whatever the list misses, so a miss in the live pass costs a few seconds of latency, not a lost commitment.

Confirmed means both passes saw it. This is what stops "I'll send it Thursday, actually make that Wednesday" from sending two follow-ups. Thursday goes to superseded, Wednesday goes out.

The threshold is the gate. 0.8 and above acts on its own. 0.5 to 0.8 waits for a rep. Below 0.5 is noise. I'd tune the number with real calls; the split between "acts alone" and "asks a human" is the part I'd keep.

Two verification paths, because Recall has two. Real-time endpoints are checked by a token in the URL. Dashboard webhooks are checked by a signature. A customer will hit both, so both are in the code.

Sqlite and no queue. It's a sample. The production section says what changes.

## What broke

My first bot used `meeting_captions` as the transcript provider and nothing streamed. That provider only produces a transcript after the call. I switched to `recallai_streaming`.

Every Recall path ends with a slash, so when I forgot to export a bot id in my shell, `/api/v1/bot//send_chat_message/` returned a plain 404 instead of telling me the id was empty. Ten minutes gone.

The first version confirmed every live commitment when the call ended, so a revised due date sent two Slack messages. That's where the superseded status came from.

The dry run stub treated "we'll need SSO before anyone logs in" as a promise from the customer. It's a requirement. The model gets this right and the regex didn't, so the stub now skips anything starting with "need".

The first real model run sent nothing. Both passes found the same promises, but the full transcript pass rephrased them, so the string matcher decided they were different commitments, superseded the live ones, and left the new wording waiting for a rep. The post-meeting call is now shown the live commitments and told to keep their wording when it's the same promise. The matcher stays a dumb string ratio on purpose; the model does the semantic part.

## Taking this to production

Schedule bots from the calendar with `join_at` instead of creating them ad hoc. Ad hoc bots can return 507 when the pool is empty.

Put a queue in front of both webhooks. Recall gives you 15 seconds to respond to a dashboard webhook and the model call can take longer than that under load. Background tasks cover it for one process; they don't cover it for a fleet.

Route by tenant with bot `metadata`. It comes back on every event, so the customer and deal ids belong there.

Batch the model calls. Several utterances per detection instead of one call per cue word.

Replace the Slack post with the CRM write. It's one function in `app/actions.py`.

Set retention on recordings and delete transcripts and drafts on a schedule. Nothing in this repo should keep a customer's call forever.

Things I didn't build on purpose: chat messages from the bot, calendar sync, and anything on Zoom or Teams beyond what the same code already does. `NOTES.md` has everything about the API and docs that surprised me while building this.
