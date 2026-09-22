# Next steps bot

A Recall.ai sample app. A meeting bot joins a sales call, commitments show up on a page while people are still talking, and after the call a second pass over the full transcript confirms them and drafts one follow-up each.

## What this is

TODO

## Why I built this one

TODO

## Run it in five minutes

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
```

1. Fill in `.env`: Recall API key, workspace verification secret, Anthropic key, a random `RT_TOKEN`, and your public URL (a static ngrok domain works: `ngrok http 8000`).
2. In the Recall dashboard, add a webhook endpoint at `PUBLIC_URL/webhooks/recall` subscribed to the `bot.*` status events plus `recording.done` and `transcript.done`.
3. Start the app and open it:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

4. Paste a Google Meet link at http://localhost:8000 and join the meeting yourself. Say "I'll send the order form by Thursday" and watch `/bots/<id>`.

No keys yet? Set `DRY_RUN=1` and use the replay below.

## How it works

Bot. `POST /bots` creates a Recall bot with `recallai_streaming` transcription and one real-time webhook endpoint for `transcript.data`, pointed at this app with a token in the URL.

Live events. Every utterance lands on `/rt`, is deduped on (bot, participant, start time, text) because Recall retries, and is saved. A word list decides whether it might contain a commitment; only then does the detector run over the last eight utterances. Anything it returns is fuzzy matched against what is already stored so the same promise is not added twice. At or above the confidence threshold it shows as proposed, between 0.5 and the threshold as unsure, below 0.5 it is dropped.

Detector. One function, `detect(utterances, mode)`, sends the prompt in `app/prompt.md` plus the transcript to the model and parses JSON back. Malformed output becomes an empty list and a log line. `DRY_RUN=1` swaps the model for a regex stub.

Post-meeting pass. `recording.done` arrives on the dashboard webhook, signature verified, deduped by webhook id. The app downloads the full transcript from the bot's recording, runs the detector once in `post_meeting` mode, matches the results against the live commitments, adds what the live pass missed as `post_meeting`, and flips `proposed` to `confirmed`.

Follow-ups. Each confirmed or post-meeting commitment gets a two line draft from the owner's point of view. Confirmed ones at or above the threshold are posted to Slack automatically and marked sent. Unsure and post-meeting ones wait on the page for a rep to approve or dismiss.

## Replay a recorded call

```bash
.venv/bin/python scripts/make_fixture.py <bot_id>
.venv/bin/python scripts/replay.py --speed 4 --finish
```

The first command saves a finished bot's transcript as `fixtures/call.json`. The second plays it back through `/rt` at real pacing (`--speed 4` is four times faster) and, with `--finish`, sends the end of call webhooks so the post-meeting pass and follow-ups run too. TODO(Shawn): `fixtures/call.json` ships as a hand written placeholder until `make_fixture.py` has been run on a real call.

## Decisions I made

TODO

## What broke

TODO

## Taking this to production

- Schedule bots from the calendar with `join_at` instead of creating them ad hoc, which avoids 507s.
- Put a queue in front of both webhooks so a slow model call never delays the next utterance.
- Route per tenant with bot `metadata`; it comes back on every event.
- Retention: delete transcripts and drafts on a schedule, and use Recall's retention config for media.
- Batch model calls: several utterances per detection instead of one call per cue.
