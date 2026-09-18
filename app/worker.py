# poll queued jobs; claim one, run pandas, store result

from __future__ import annotations

import os
import time
from pathlib import Path

from app.analysis import analyse_csv
from app.db import Database

BASE_DIR = Path(__file__).resolve().parent.parent
POLL_SECONDS = 1.0


def run_once(db: Database) -> bool:
    # True when we claimed one (ok or failed)
    claimed = db.claim_next_job()
    if claimed is None:
        return False
    job_id, _dataset_id, file_path = claimed
    try:
        summary = analyse_csv(Path(file_path))
        db.mark_job_succeeded(job_id, summary)
    except Exception as exc:
        # str(exc) is what the job page shows
        db.mark_job_failed(job_id, str(exc))
    return True


def run_loop(db: Database, *, poll_seconds: float = POLL_SECONDS) -> None:
    # dont sleep while there is a backlog
    while True:
        ran = run_once(db)
        if not ran:
            time.sleep(poll_seconds)


def main() -> None:
    db_path = Path(
        os.environ.get("LAB_DATA_DESK_DB", BASE_DIR / "data" / "lab-data-desk.db")
    )
    db = Database(db_path)
    db.init()
    print(f"worker watching {db_path}", flush=True)
    run_loop(db)


if __name__ == "__main__":
    main()
