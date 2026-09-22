"""Save a finished bot's transcript from Recall as fixtures/call.json, for scripts/replay.py and the tests.

    .venv/bin/python scripts/make_fixture.py <bot_id>

Needs RECALL_API_KEY in .env. Run it after the bot's recording.done arrived; before that there is no
transcript download url and this prints the recording status instead.
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import recall  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    bot = recall.get_bot(sys.argv[1])
    for recording in bot.get("recordings") or []:
        shortcut = (recording.get("media_shortcuts") or {}).get("transcript") or {}
        url = (shortcut.get("data") or {}).get("download_url")
        if not url:
            print(f"recording {recording['id']}: status {json.dumps(recording.get('status'))}, "
                  f"transcript status {json.dumps(shortcut.get('status'))}")
            continue
        transcript = recall.download(url)
        out = ROOT / "fixtures" / "call.json"
        out.write_text(json.dumps(transcript, indent=2) + "\n")
        speakers = sorted({(e.get("participant") or {}).get("name") or "?" for e in transcript})
        print(f"wrote {out}: {len(transcript)} utterances from {', '.join(speakers)}")
        return
    sys.exit("no transcript download url yet; wait for recording.done and try again")


if __name__ == "__main__":
    main()
