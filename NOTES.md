# Notes

One line per thing about the Recall API or docs that was confusing, missing, or surprising. Newest at the bottom.

- Real-time webhook URLs need a trailing slash before the query string (`/rt/?token=...`); the docs say create bot returns 400 without it. The app answers on both `/rt` and `/rt/`.
- The real-time endpoints page says `real_time_endpoints` in prose and `realtime_endpoints` in every example. The example is the real field name.
- Two verification mechanisms. Real-time endpoints are verified by the token in the URL Recall calls back (they also carry signature headers once a workspace secret exists). Dashboard webhooks are signed with the workspace verification secret, or on legacy workspaces (created before 2025-12-15) with the per endpoint Svix secret.
- Signature headers can be `webhook-id/timestamp/signature` or `svix-id/timestamp/signature`. Signed string is `id.timestamp.rawbody`, HMAC SHA256 with the base64 part of the `whsec_` secret, header may hold several space separated `v1,<sig>` entries.
- Dashboard webhook payloads carry no event id, so dedupe uses the `webhook-id` (or `svix-id`) header.
- Status change webhooks call the timestamp `updated_at`; the same entry in the bot object's `status_changes[]` is `created_at`. Stored here as `created_at`.
- Retry policy differs. Real-time webhooks retry 60 times, 1 second apart, then the endpoint is marked failed with no manual retry. Dashboard webhooks retry with backoff for 24 hours.
- Dashboard webhooks time out after 15 seconds. Respond first, do the work in a background task.
- `recallai_streaming` defaults to `prioritize_accuracy`, which runs an async model under the hood and batches utterances. `prioritize_low_latency` gives 1 to 3 second utterances but only supports `language_code: en`.
- The transcript download schema (participant + words per utterance) is the same shape as the live `data.data` object, so a downloaded transcript replays as live events unchanged.
- The transcript download URL lives at `recordings[].media_shortcuts.transcript.data.download_url`, is null until the recording is done, and is pre-signed (no Authorization header).
- `meeting_url` on a bot response is an object, not a string. `meeting_url.platform` is one of google_meet, zoom, microsoft_teams, microsoft_teams_live, webex, goto_meeting. It is cleared a few days after the call.
- Create bot returns 507 when the ad hoc pool is empty. Docs say retry every 30 seconds up to 10 times; production should schedule with `join_at`.
- The Retrieve Bot reference says polling for bot status is an anti-pattern, which is why the page never asks Recall for status and relies on webhooks.
- Every API path ends with a slash (`/api/v1/bot/`). Posting without it can turn into a redirect.
- On docs/sub-codes the Zoom tables are HTML `<Table>` blocks while the rest are markdown tables, so a scraper has to handle both.
- The create bot reference says most transcription features are unsupported in `prioritize_low_latency` mode (no diarization options, English only). Good enough for a live demo, switch to the default mode if names or languages matter.
- The dashboard webhook sends events for every bot in the workspace, not just the ones this app created, so the handler ignores bot ids it does not know.
- `recording.done` says all media is available, but the docs for real-time transcription point at `transcript.done` for the transcript. The app listens to both and runs the post-meeting pass on whichever arrives first.
