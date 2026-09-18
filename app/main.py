# routes + create_app(); worker lives in worker.py

from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import Database

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    # curl / tests often send */* - treat explicit json query as json too
    return request.query_params.get("format") == "json"


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _dataset_payload(d) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "owner": d.owner,
        "file_path": d.file_path,
        "schema_notes": d.schema_notes or "",
        "created_at": d.created_at,
        "tags": d.tags,
    }


def _job_payload(j) -> dict:
    return {
        "id": j.id,
        "dataset_id": j.dataset_id,
        "status": j.status,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "error_message": j.error_message,
        "result": j.result,
    }


def _pretty_result(result: Optional[dict]) -> str:
    if not result:
        return ""
    return json.dumps(result, indent=2)


def create_app(
    *,
    db_path: Optional[Path] = None,
    uploads_dir: Optional[Path] = None,
) -> FastAPI:
    # tests / docker pass paths; env for compose
    database_path = Path(
        db_path
        or os.environ.get("LAB_DATA_DESK_DB", BASE_DIR / "data" / "lab-data-desk.db")
    )
    uploads = Path(
        uploads_dir
        or os.environ.get("LAB_DATA_DESK_UPLOADS", BASE_DIR / "uploads")
    )
    uploads.mkdir(parents=True, exist_ok=True)

    db = Database(database_path)
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        db.init()
        yield

    app = FastAPI(title="lab-data-desk", lifespan=lifespan)
    app.state.db = db
    app.state.uploads = uploads

    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    @app.get("/health")
    def health() -> dict:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request, tag: Optional[str] = None) -> Response:
        datasets = db.list_datasets(tag=tag or None)
        if _wants_json(request):
            return JSONResponse({"datasets": [_dataset_payload(d) for d in datasets]})
        return templates.TemplateResponse(
            request,
            "datasets.html",
            {"datasets": datasets, "tag_filter": tag or ""},
        )

    @app.get("/datasets")
    def list_datasets(request: Request, tag: Optional[str] = None) -> Response:
        datasets = db.list_datasets(tag=tag or None)
        if _wants_json(request):
            return JSONResponse({"datasets": [_dataset_payload(d) for d in datasets]})
        # html lives on / with the register form
        qs = f"?tag={tag}" if tag else ""
        return RedirectResponse(url=f"/{qs}", status_code=302)

    @app.post("/datasets")
    async def register_dataset(
        request: Request,
        file: UploadFile = File(...),
        name: str = Form(...),
        owner: str = Form(...),
        tags: str = Form(""),
        schema_notes: str = Form(""),
    ) -> Response:
        if not name.strip() or not owner.strip():
            raise HTTPException(status_code=400, detail="name and owner are required")
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="upload a .csv file")

        # random prefix - never trust the client path as the on-disk name
        stored = f"{secrets.token_hex(8)}_{Path(file.filename).name}"
        dest = uploads / stored
        dest.write_bytes(await file.read())

        dataset = db.create_dataset(
            name=name.strip(),
            owner=owner.strip(),
            file_path=str(dest.resolve()),
            schema_notes=schema_notes.strip(),
            tags=_parse_tags(tags),
        )

        if _wants_json(request):
            return JSONResponse(_dataset_payload(dataset), status_code=201)
        # 303 so a refresh does not re-POST the multipart
        return RedirectResponse(url=f"/datasets/{dataset.id}", status_code=303)

    @app.get("/datasets/{dataset_id}")
    def get_dataset(request: Request, dataset_id: int) -> Response:
        dataset = db.get_dataset(dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        if _wants_json(request):
            return JSONResponse(_dataset_payload(dataset))
        jobs = [j for j in db.list_jobs() if j.dataset_id == dataset_id]
        return templates.TemplateResponse(
            request,
            "dataset_detail.html",
            {"dataset": dataset, "jobs": jobs},
        )

    @app.post("/datasets/{dataset_id}/jobs")
    def enqueue_job(request: Request, dataset_id: int) -> Response:
        dataset = db.get_dataset(dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        job = db.enqueue_job(dataset_id)
        if _wants_json(request):
            return JSONResponse(_job_payload(job), status_code=201)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs")
    def list_jobs(request: Request) -> Response:
        jobs = db.list_jobs()
        if _wants_json(request):
            return JSONResponse({"jobs": [_job_payload(j) for j in jobs]})
        return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})

    @app.get("/jobs/{job_id}")
    def get_job(request: Request, job_id: int) -> Response:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if _wants_json(request):
            return JSONResponse(_job_payload(job))
        dataset = db.get_dataset(job.dataset_id)
        return templates.TemplateResponse(
            request,
            "job_detail.html",
            {
                "job": job,
                "dataset": dataset,
                "summary_pretty": _pretty_result(job.result),
            },
        )

    @app.get("/jobs/{job_id}/export")
    def export_job_result(job_id: int) -> Response:
        # raw summary_json - no html wrapper
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.result is None:
            raise HTTPException(status_code=404, detail="no result yet")
        return JSONResponse(job.result)

    return app


# default app for uvicorn app.main:app
app = create_app()
