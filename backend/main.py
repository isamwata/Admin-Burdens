import json
import os
import uuid
from datetime import date, datetime

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .predictor import predict_documents
from .scraper import scrape_documents

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="RIA Assessments API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(_root, "config.json")) as f:
    CONFIG = json.load(f)

SCRAPED_DIR = os.path.join(_root, "scraped_data")
PREDICT_DIR = os.path.join(_root, "predictions")
os.makedirs(SCRAPED_DIR, exist_ok=True)
os.makedirs(PREDICT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# In-memory job store  {job_id: {...}}
# ---------------------------------------------------------------------------
JOBS: dict = {}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class ScrapeRequest(BaseModel):
    start_date: date
    end_date: date
    doc_types: list[str] = ["Koninklijk besluit"]


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
def _run_scrape(job_id: str, req: ScrapeRequest):
    def progress(done, total):
        JOBS[job_id]["progress"] = int(done / total * 100)
        JOBS[job_id]["progress_text"] = f"Fetching detail {done}/{total}"

    try:
        JOBS[job_id]["status"] = "scraping"
        JOBS[job_id]["progress_text"] = "Launching browser…"

        results = scrape_documents(
            start_date=datetime.combine(req.start_date, datetime.min.time()),
            end_date=datetime.combine(req.end_date, datetime.min.time()),
            doc_types=req.doc_types,
            url_searchpage=CONFIG["scraping"]["url_searchpage"],
            url_detail_page=CONFIG["scraping"]["url_detail_page"],
            progress_callback=progress,
        )

        filename = f"{req.start_date}_{req.end_date}_scraping_results.xlsx"
        filepath = os.path.join(SCRAPED_DIR, filename)
        pd.DataFrame(results).to_excel(filepath)

        JOBS[job_id].update(
            status="done", progress=100,
            result=results, count=len(results),
            excel_file=filepath, filename=filename,
        )
    except Exception as exc:
        JOBS[job_id].update(status="error", error=str(exc))


def _run_predict(job_id: str, scrape_job_id: str):
    try:
        JOBS[job_id]["status"] = "running"
        scrape_job = JOBS.get(scrape_job_id, {})

        if scrape_job.get("status") != "done":
            raise ValueError("Scrape job is not complete")

        dataset = pd.DataFrame(scrape_job["result"])
        result = predict_documents(dataset, CONFIG["predictions"])

        ts = str(datetime.now().timestamp()).replace(".", "_")
        filename = f"{ts}_predictions.xlsx"
        filepath = os.path.join(PREDICT_DIR, filename)
        result.to_excel(filepath)

        JOBS[job_id].update(
            status="done",
            result=result.to_dict(orient="records"),
            excel_file=filepath,
            filename=filename,
        )
    except Exception as exc:
        JOBS[job_id].update(status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/document-types")
def document_types():
    return {"types": CONFIG["scraping"]["document_types"]}


@app.post("/api/scrape")
def start_scrape(req: ScrapeRequest, background_tasks: BackgroundTasks):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "progress": 0, "progress_text": "", "error": None}
    background_tasks.add_task(_run_scrape, job_id, req)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Omit large result/filepath from status response
    return {k: v for k, v in job.items() if k not in ("result", "excel_file")}


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = job.get("result") or []
    return {"data": rows[:10], "total": len(rows)}


@app.post("/api/predict/{scrape_job_id}")
def start_predict(scrape_job_id: str, background_tasks: BackgroundTasks):
    scrape_job = JOBS.get(scrape_job_id)
    if not scrape_job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    if scrape_job.get("status") != "done":
        raise HTTPException(status_code=400, detail="Scrape job is not complete yet")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "progress": 0, "error": None}
    background_tasks.add_task(_run_predict, job_id, scrape_job_id)
    return {"job_id": job_id}


@app.get("/api/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Results not available")
    filepath = job.get("excel_file")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        filepath,
        filename=job["filename"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
