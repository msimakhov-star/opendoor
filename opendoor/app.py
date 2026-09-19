"""Open Door local API. Binds 127.0.0.1 only.
  uv run python -m opendoor.app [--fake] [--port 8000]
"""
import argparse, json, os, re, threading, time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from opendoor import pipeline
from opendoor.letter import draft_letter

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LIVE: dict[str, dict] = {}  # run_id -> {"events": [...], "done": bool} for runs started by this process
app = FastAPI(title="Open Door")


class RunRequest(BaseModel):
    postcode: str = "E13"
    limit: int = 40


def run_dir(run_id: str) -> Path:
    """Frozen runs win over live ones. The id is checked because it comes from the URL and becomes a path."""
    if not re.fullmatch(r"[\w.-]+", run_id) or ".." in run_id:
        raise HTTPException(400, "bad run id")
    for base in (DATA / "frozen", DATA / "runs"):
        if (base / run_id).is_dir():
            return base / run_id
    raise HTTPException(404, "no such run")


@app.get("/")
def index():
    return FileResponse(ROOT / "opendoor" / "ui" / "index.html")


@app.post("/api/run")
def start_run(req: RunRequest):
    prefixes = pipeline.parse_prefixes(req.postcode)
    if not prefixes:
        raise HTTPException(400, "Give a postcode or postcode area such as E13 or SE.")
    limit = max(1, min(req.limit, 1500))
    run_id = time.strftime("%Y%m%d-%H%M%S-") + "-".join(prefixes)[:20]
    state = LIVE[run_id] = {"events": [], "done": False}

    def work():
        try:
            pipeline.run(prefixes, limit, run_id, state["events"].append, fake=bool(os.environ.get("OPENDOOR_FAKE")))
        finally:
            state["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/run/{run_id}")
def poll(run_id: str, since: int = 0):
    if run_id in LIVE:
        s = LIVE[run_id]
        done = s["done"]  # read before the slice, so a final event can never be missed
        return {"events": s["events"][max(0, since):], "done": done}
    f = run_dir(run_id) / "events.jsonl"  # an earlier or frozen run: replay from disk, no network needed
    events = [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []
    return {"events": events[max(0, since):], "done": True}


def _results(run_id: str) -> dict:
    f = run_dir(run_id) / "results.json"
    if not f.exists():
        raise HTTPException(404, "run has no results yet")
    return json.loads(f.read_text())


@app.get("/api/runs/{run_id}/results")
def results(run_id: str):
    return _results(run_id)


@app.get("/api/runs")
def runs():
    out = []
    for base, frozen in ((DATA / "frozen", True), (DATA / "runs", False)):
        for d in sorted(base.glob("*/results.json"), reverse=True):
            s = json.loads(d.read_text()).get("summary", {})
            out.append({"run_id": d.parent.name, "frozen": frozen, "practices": s.get("total"), **{k: s.get(k) for k in
                        ("postcode_prefixes", "total", "counts", "wall_secs", "finished_at", "fake")}})
    return {"runs": out}


@app.get("/shots/{run_id}/{file}")
def shot(run_id: str, file: str):
    if not re.fullmatch(r"[\w-]+\.png", file):
        raise HTTPException(400, "bad file name")
    p = run_dir(run_id) / "shots" / file
    if not p.exists():
        raise HTTPException(404, "no such screenshot")
    return FileResponse(p, media_type="image/png")


@app.get("/api/letter/{run_id}/{code}", response_class=PlainTextResponse)
def letter(run_id: str, code: str):
    r = next((x for x in _results(run_id)["results"] if x["code"] == code), None)
    if not r:
        raise HTTPException(404, "no such practice in this run")
    text = draft_letter(r)
    if not text:
        raise HTTPException(404, "No letter for this practice: its page has no verified wording that contradicts the NHS guidance on nhs.uk.")
    return text


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="runs use local probe data and a keyword classifier: no Modal, no Gemini")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.fake:
        os.environ["OPENDOOR_FAKE"] = "1"
    uvicorn.run(app, host="127.0.0.1", port=a.port)
