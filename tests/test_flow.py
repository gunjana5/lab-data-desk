# create_app + tmp_path; worker run_once in-process

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.worker import run_once

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "assays.csv"


@pytest.fixture
def client(tmp_path: Path):
    # fresh db + uploads per test - never touch the real data/
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    app = create_app(db_path=tmp_path / "test.db", uploads_dir=uploads)
    with TestClient(app) as c:
        yield c


def _register(
    client: TestClient,
    *,
    content: bytes | None = None,
    filename: str = "assays.csv",
    name: str = "pilot plate",
    owner: str = "gunjana",
    tags: str = "elisa, pilot",
    schema_notes: str = "sample_id, assay, value, run_date",
):
    if content is None:
        content = SAMPLE.read_bytes()
    return client.post(
        "/datasets",
        files={"file": (filename, BytesIO(content), "text/csv")},
        data={
            "name": name,
            "owner": owner,
            "tags": tags,
            "schema_notes": schema_notes,
        },
        headers={"Accept": "application/json"},
    )


def test_register_enqueue_worker_succeeds(client: TestClient):
    # happy path: register -> enqueue -> run_once -> succeeded result
    up = _register(client)
    assert up.status_code == 201
    body = up.json()
    assert body["name"] == "pilot plate"
    assert "elisa" in body["tags"]
    assert "pilot" in body["tags"]
    dataset_id = body["id"]

    listed = client.get("/datasets", headers={"Accept": "application/json"})
    assert listed.status_code == 200
    assert listed.json()["datasets"][0]["id"] == dataset_id

    detail = client.get(f"/datasets/{dataset_id}", headers={"Accept": "application/json"})
    assert detail.status_code == 200
    assert detail.json()["owner"] == "gunjana"

    job_res = client.post(
        f"/datasets/{dataset_id}/jobs",
        headers={"Accept": "application/json"},
    )
    assert job_res.status_code == 201
    job = job_res.json()
    assert job["status"] == "queued"
    job_id = job["id"]

    db = client.app.state.db
    assert run_once(db) is True

    done = client.get(f"/jobs/{job_id}", headers={"Accept": "application/json"})
    assert done.status_code == 200
    payload = done.json()
    assert payload["status"] == "succeeded"
    assert payload["error_message"] is None
    result = payload["result"]
    assert result["row_count"] == 120
    assert "per_assay" in result
    assert "ELISA" in result["per_assay"]
    assert "missing_values" in result
    assert "outliers" in result
    assert result["per_assay"]["ELISA"]["count"] > 0

    exported = client.get(f"/jobs/{job_id}/export")
    assert exported.status_code == 200
    assert exported.json()["row_count"] == 120


def test_worker_fails_on_bad_csv(client: TestClient):
    # failure path: bad columns -> failed + error_message
    bad = b"foo,bar\n1,2\n"
    up = _register(client, content=bad, filename="bad.csv", tags="broken")
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    job_id = client.post(
        f"/datasets/{dataset_id}/jobs",
        headers={"Accept": "application/json"},
    ).json()["id"]

    assert run_once(client.app.state.db) is True

    payload = client.get(
        f"/jobs/{job_id}", headers={"Accept": "application/json"}
    ).json()
    assert payload["status"] == "failed"
    assert payload["result"] is None
    assert payload["error_message"]
    assert "missing columns" in payload["error_message"].lower()


def test_filter_datasets_by_tag(client: TestClient):
    _register(client, name="a", tags="alpha")
    _register(client, name="b", tags="beta")
    res = client.get(
        "/datasets",
        params={"tag": "alpha"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    names = [d["name"] for d in res.json()["datasets"]]
    assert names == ["a"]


def test_claim_race_second_worker_loses(client: TestClient):
    # two claim attempts on one queued job - only the first wins
    dataset_id = _register(client).json()["id"]
    job_id = client.post(
        f"/datasets/{dataset_id}/jobs",
        headers={"Accept": "application/json"},
    ).json()["id"]

    db = client.app.state.db
    first = db.claim_next_job()
    second = db.claim_next_job()

    assert first is not None
    assert first[0] == job_id
    assert second is None

    job = db.get_job(job_id)
    assert job is not None
    assert job.status == "running"
    # nothing left queued - a second worker would idle
    assert run_once(db) is False


def test_worker_fails_when_upload_missing(client: TestClient):
    # file gone from uploads/ -> FileNotFoundError -> failed
    up = _register(client)
    dataset_id = up.json()["id"]
    file_path = Path(up.json()["file_path"])
    assert file_path.is_file()
    file_path.unlink()

    job_id = client.post(
        f"/datasets/{dataset_id}/jobs",
        headers={"Accept": "application/json"},
    ).json()["id"]

    assert run_once(client.app.state.db) is True

    payload = client.get(
        f"/jobs/{job_id}", headers={"Accept": "application/json"}
    ).json()
    assert payload["status"] == "failed"
    assert payload["result"] is None
    assert "not found" in payload["error_message"].lower()

    # no summary to export on a failed job
    exported = client.get(f"/jobs/{job_id}/export")
    assert exported.status_code == 404


def test_export_json_shape_and_404_before_result(client: TestClient):
    dataset_id = _register(client).json()["id"]
    job_id = client.post(
        f"/datasets/{dataset_id}/jobs",
        headers={"Accept": "application/json"},
    ).json()["id"]

    early = client.get(f"/jobs/{job_id}/export")
    assert early.status_code == 404

    assert run_once(client.app.state.db) is True

    exported = client.get(f"/jobs/{job_id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    body = exported.json()
    assert set(body.keys()) == {
        "row_count",
        "missing_values",
        "per_assay",
        "outliers",
    }
    assert body["row_count"] == 120
    assert set(body["missing_values"].keys()) == {
        "sample_id",
        "assay",
        "value",
        "run_date",
    }
    assert isinstance(body["per_assay"], dict)
    assert isinstance(body["outliers"], list)

    job = client.get(
        f"/jobs/{job_id}", headers={"Accept": "application/json"}
    ).json()
    assert body == job["result"]
