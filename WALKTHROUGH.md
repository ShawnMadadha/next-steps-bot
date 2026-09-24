# Walkthrough

Kept up to date while building. This is the file to read before presenting.

## What it is, in one breath

A Recall bot joins a sales call, commitments show up on a page while people are still talking, a second pass over the full transcript confirms them after the call, and confirmed ones go to Slack on their own while the rest wait for a rep. Built for a Recall customer like HubSpot who would keep the pipeline and swap the page and the Slack post for their own CRM.

## Files

- `app/main.py`: the routes and the two pages. Exists because something has to create bots, render the page, and take the approve and dismiss clicks; it does nothing else.
- `app/recall.py`: three calls to Recall: create a bot, read a bot, download a transcript. Exists so every Recall request and every Recall error message lives in one place.
- `app/realtime.py`: the `/rt` handler for live transcript events: token check, dedupe, cue gate, the eight utterance window, the fuzzy match. Exists because the live path has its own rules and they should be readable top to bottom.
- `app/webhooks.py`: the dashboard webhook: signature check, dedupe by webhook id, status changes, and the post-meeting pass that reconciles, drafts, and sends. Exists because Recall's second kind of webhook is verified differently and triggers the end of call work.
- `app/detector.py`: one function, `detect`, that sends the prompt and the transcript to the model and parses JSON back, plus the cue word list and the `DRY_RUN=1` regex stub. Exists so the model is behind one door and the app runs with no key.
- `app/prompt.md`: the prompt. Exists as a file so it can be read and edited without touching code.
- `app/actions.py`: `send_followup`, the one action: post a draft to Slack by bot token or by incoming webhook. Exists because a customer replaces this file with their CRM write.
- `app/store.py`: the sqlite schema and every query. Exists so no other file writes SQL.
- `app/subcodes.py`: Recall status codes and sub codes to plain English. Exists so the page can say "the host removed the bot" instead of `bot_kicked_from_call`.
- `templates/index.html`, `templates/bot.html`: the two pages, plain HTML with one small CSS block, the bot page refreshes itself every three seconds while the call is live.
- `scripts/make_fixture.py`: saves a finished bot's transcript as `fixtures/call.json`. Exists so a real call can be replayed.
- `scripts/replay.py`: plays the fixture through `/rt` at real pacing and, with `--finish`, sends signed end of call webhooks. Exists as the demo fallback and the basis of the tests.
- `fixtures/call.json`: a real two person call, saved exactly as Recall returned it.
- `tests/`: dedupe, the cue gate, the fuzzy match, the parser on malformed output, signature verification, reconciliation, the Slack paths, owner ids.
- `NOTES.md`: every Recall API or docs surprise, one line each. `README.md`: the write-up. `AGENTS.md`: how to work on the repo with a coding agent.

## Decisions

- Live plus post-meeting: picked running the model during the call and again over the full transcript at the end over post-meeting only. Gained commitments on screen while the call is happening. Gave up simplicity: two passes have to be reconciled.
- The cue gate: picked a word list in front of the model over calling the model on every utterance. Gained most utterances never costing a model call. Gave up recall in the live pass (a promise without a cue word waits for the full pass), and the list is literal: "by Friday" is a cue, "by Thursday" is not.
- Eight utterance window: picked the last eight utterances over the whole transcript so far. Gained a small, fast prompt. Gave up context; the full pass at the end covers what the window missed.
- Confirmed means both passes saw it: picked confirming only live commitments the full pass also returned over confirming everything live at the end of the call. Gained "Thursday, actually Wednesday" sending once. Gave up sending live commitments the full pass worded differently, which is why the full pass is shown the live list and asked to keep its wording.
- The threshold is the gate: picked one number, 0.8, deciding what the app acts on alone over always asking a human. Gained an honest automation story. Gave up a rep review on the high confidence ones.
- Fuzzy matching by string ratio plus containment: picked `difflib` over embeddings or another model call. Gained no extra dependency and an explainable rule. Gave up robustness to rephrasing, which the prompt now handles.
- Owners keyed by participant id, name for display: picked the id Recall gives each participant over the display name. Gained two people with one name staying two people. Gave up nothing but one column.
- Background tasks and a lock instead of a queue: picked FastAPI background tasks plus one lock over a queue. Gained a one process app. Gave up scale; the production section of the README says a queue goes in front of both webhooks.
- Sqlite, plain functions, no ORM: picked the standard library over SQLAlchemy. Gained a schema anyone can read in thirty seconds. Gave up migrations; there is one `ALTER TABLE` for the owner id column.
- Two verification paths: picked a token in the URL for real-time endpoints and the Svix signature for dashboard webhooks, because Recall offers exactly those two. Gave up nothing; both are in the code because a customer hits both.
- The bot never speaks in the meeting: picked the rep's screen over posting into meeting chat. Gained a rep who confirms out loud. Gave up an in-meeting surface.
- Slack by bot token or webhook URL: picked supporting both over one. Gained working with whatever Slack hands out (the CLI gives a token, the settings page gives a webhook URL). Gave up ten lines.

## Numbers

- `WINDOW` = 8 in `app/realtime.py`: utterances the model sees per live call. A guess: enough to see "I'll send it Thursday" and the "actually Wednesday" that follows; tune against how often the live pass misses a revision.
- `MATCH_RATIO` = 0.8 in `app/realtime.py`: `SequenceMatcher` ratio above which two actions are the same promise. "send the signed order form" and "send signed order form" score 0.92; "send the DPA by Thursday" and "send the DPA by Wednesday" score 0.82, which is why due dates are compared separately. Tune against duplicates on the page.
- `CONFIDENCE_THRESHOLD` = 0.8 in `.env`: at or above, a live commitment is proposed and a confirmed one is sent on its own. A guess I would tune with real calls; the split between acting alone and asking a human is the part to keep.
- 0.5 in `status_for`, `app/realtime.py`: below it the model's finding is dropped, between 0.5 and the threshold it is unsure and waits for a rep.
- 3 seconds in `templates/bot.html`: the meta refresh while the bot is live. Fast enough to feel live, slow enough not to matter.
- 1 second in `split_on_pauses`, `scripts/replay.py`: a gap between words longer than this splits a downloaded speaker turn into fragments, which is what the live feed looked like.
- 8 seconds and 5 seconds in `scripts/replay.py`: the longest pause a replay waits between utterances, and the settle before the end of call webhooks so the last model calls finish first.
- 30, 60 and 15 seconds: httpx timeouts for Recall calls, the transcript download, and Slack.
- 4096 in `app/detector.py`: `max_tokens` for the model reply, more than a full call's worth of commitments needs.
- 0.9 and 0.6 in the stub, `app/detector.py`: a first person promise and a "let me", chosen so the stub produces one proposed and one unsure item on any demo transcript. Not tuned; the stub is a stand-in.
- 5 letters in `_keywords`, `app/detector.py`: the stub's idea of a distinctive word when it drops a revised promise.

## External calls and what happens when they fail

- `app/recall.py` calls Recall create bot. On failure: the response body comes back as a readable 502 on the page, including 507 when the ad hoc pool is empty; production would schedule with `join_at`. Missing key: a plain message instead of a traceback.
- `app/recall.py` calls Recall get bot and the transcript download URL, from the post-meeting pass. On failure: the pass logs and leaves the bot without `post_meeting_at`, so the next `recording.done` or `transcript.done` runs it again. No URL yet: same.
- Recall calls `/rt` (`app/realtime.py`). Wrong or missing token: 401. Recall retries real-time events 60 times, one second apart; every retry is deduped on (bot, participant, start time, text), so a retry stores nothing and calls no model. The response goes back before any work runs.
- Recall calls `/webhooks/recall` (`app/webhooks.py`). No signature or a bad one: 401 with the reason in the log. Duplicate delivery: deduped by the Svix `webhook-id` header. Unknown bot: ignored with a 200. Wrong secret for a whole call: Recall retries on a backoff that reached 25 minutes, so the timeline stays empty for the length of a demo.
- `app/detector.py` calls the model. On any API error: log and return no commitments; the post-meeting pass gets another shot. Malformed JSON: log and return no commitments, never a failed webhook.
- `app/actions.py` calls Slack. On failure: `RuntimeError` with Slack's error, the commitment stays confirmed with its approve button so a rep can retry; the log says so. Slack `not_in_channel` means the bot was never invited.
- `app/store.py` is sqlite in one file. Concurrent writes are serialized by sqlite; the detection lock keeps two live events from adding the same commitment.

## Things that broke while building

- Port 8000 refused on this Mac was Docker holding it; everything runs on 8765.
- Every dashboard webhook came back 401 was the local placeholder secret still in `.env`; fixed by the real workspace secret, and the retries only came back 25 minutes later.
- The bot never left an empty meeting was two bots in the same call counting each other as participants; fixed by Remove Bot From Call, and by sending one bot.
- The downloaded transcript duplicated the page's transcript was Recall merging a whole speaker turn into one utterance while the live feed had split it on pauses; fixed by only backfilling when the live path got nothing, and by replay splitting turns on pauses.
- The stub swallowed a whole call into one action was low latency text having no punctuation; fixed by also stopping at "and", "but", "so", "or", "then".
- The first real model run sent nothing was the two passes wording the same promise differently; fixed by showing the full pass the live commitments and, for fragments, matching by containment.
- `joining_call` stored twice was sqlite treating NULL sub codes as distinct in the unique index; fixed by storing an empty string.
- Every utterance attributed to the host was the diarization flag missing on the bot; fixed by one line in `create_bot`.
- Two participants merged into one person was owners keyed by display name after I joined from two devices on one account; fixed by keying owners on participant id.
- A promise split across two fragments was seen by the model only as its first half was the cue list being literal ("by Thursday" is not a cue); the next cue bearing utterance re-ran the window and got the full version.
- Nothing arrived after a call was the dashboard endpoint pointing at a previous tunnel hostname; fixed by a static ngrok domain set once.
