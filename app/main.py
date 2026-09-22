import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import actions, realtime, recall, store, subcodes, webhooks

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full request URLs, and a Slack webhook URL is a secret

PLATFORMS = {
    "google_meet": "Google Meet",
    "zoom": "Zoom",
    "microsoft_teams": "Microsoft Teams",
    "microsoft_teams_live": "Teams Live",
    "webex": "Webex",
    "goto_meeting": "GoTo Meeting",
}
STATUS_TEXT = {"post_meeting": "found after the call", "superseded": "superseded (revised or not found in full transcript)"}


@asynccontextmanager
async def lifespan(app):
    store.init()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(realtime.router)
app.include_router(webhooks.router)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@app.get("/")
def index(request: Request):
    bots = [dict(b) | {"status_text": subcodes.describe(b["status"]) if b["status"] else "created"}
            for b in store.list_bots()]
    return templates.TemplateResponse(request, "index.html", {"bots": bots})


@app.post("/bots")
def create_bot(meeting_url: str = Form(...)):
    try:
        data = recall.create_bot(meeting_url.strip())
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    platform = data["meeting_url"]["platform"]
    store.add_bot(data["id"], meeting_url.strip(), PLATFORMS.get(platform, platform))
    for change in data.get("status_changes", []):
        store.add_status_change(data["id"], change["code"], change.get("sub_code"), change["created_at"])
    return RedirectResponse(f"/bots/{data['id']}", status_code=303)


@app.get("/bots/{bot_id}")
def bot_page(request: Request, bot_id: str):
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(status_code=404)
    timeline = [{"created_at": s["created_at"], "text": subcodes.describe(s["code"], s["sub_code"])}
                for s in store.list_status_changes(bot_id)]
    rows = store.list_commitments(bot_id)
    utterances = store.list_utterances(bot_id)
    # Two participants with one display name get their participant id appended so the page can tell them apart.
    ids_by_name = {}
    for name, pid in [(c["owner"], c["owner_id"]) for c in rows] + [(u["speaker"], u["participant_id"]) for u in utterances]:
        ids_by_name.setdefault(name, set()).add(pid)
    def label(name, pid):
        return f"{name} (#{pid})" if pid is not None and len(ids_by_name.get(name, ())) > 1 else name
    commitments = [dict(c) | {"status_text": status_text(c), "owner_label": label(c["owner"], c["owner_id"])} for c in rows]
    return templates.TemplateResponse(request, "bot.html", {
        "bot": bot,
        "timeline": timeline,
        "commitments": commitments,
        "utterances": [dict(u) | {"speaker_label": label(u["speaker"], u["participant_id"])} for u in utterances],
    })


@app.post("/commitments/{commitment_id}/approve")
def approve(commitment_id: int):
    c = store.get_commitment(commitment_id)
    if c is None or not c["followup_draft"]:
        raise HTTPException(status_code=404)
    if c["status"] != "sent":
        try:
            actions.send_followup(c)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        store.set_status(commitment_id, "sent", "rep")
    return RedirectResponse(f"/bots/{c['bot_id']}", status_code=303)


@app.post("/commitments/{commitment_id}/dismiss")
def dismiss(commitment_id: int):
    c = store.get_commitment(commitment_id)
    if c is None:
        raise HTTPException(status_code=404)
    if c["status"] != "sent":
        store.set_status(commitment_id, "dismissed")
    return RedirectResponse(f"/bots/{c['bot_id']}", status_code=303)


def status_text(c):
    if c["status"] == "sent":
        return "sent automatically" if c["sent_via"] == "auto" else "sent by rep"
    return STATUS_TEXT.get(c["status"], c["status"])
