# pandas summary - pure so worker + tests share it

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

EXPECTED_COLS = {"sample_id", "assay", "value", "run_date"}
OUTLIER_Z = 3.0


def analyse_csv(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"dataset file not found: {path}")

    df = pd.read_csv(path)
    missing_cols = EXPECTED_COLS - set(df.columns)
    if missing_cols:
        raise ValueError(f"CSV is missing columns: {sorted(missing_cols)}")

    # fixed column order so json keys dont shuffle between runs
    missing_values = {
        col: int(df[col].isna().sum()) for col in ("sample_id", "assay", "value", "run_date")
    }

    values = pd.to_numeric(df["value"], errors="coerce")
    work = df.assign(value=values)

    per_assay: dict[str, dict[str, float | int]] = {}
    grouped = work.groupby("assay", dropna=False)["value"]
    for assay, series in grouped:
        clean = series.dropna()
        count = int(clean.shape[0])
        if count == 0:
            mean = 0.0
            std = 0.0
        else:
            mean = float(clean.mean())
            # ddof=0 so tiny groups match across pytest runs
            std = float(clean.std(ddof=0)) if count > 1 else 0.0
        per_assay[str(assay)] = {
            "count": count,
            "mean": round(mean, 6),
            "std": round(std, 6),
        }

    # z vs that assay's own mean/std
    outliers: list[dict[str, Any]] = []
    for _idx, row in work.iterrows():
        assay = row["assay"]
        val = row["value"]
        if pd.isna(val) or assay not in per_assay:
            continue
        stats = per_assay[str(assay)]
        std = float(stats["std"])
        mean = float(stats["mean"])
        if std <= 0:
            continue
        z = abs((float(val) - mean) / std)
        if z > OUTLIER_Z:
            outliers.append(
                {
                    "sample_id": None if pd.isna(row["sample_id"]) else str(row["sample_id"]),
                    "assay": str(assay),
                    "value": round(float(val), 6),
                    "z_score": round(z, 4),
                }
            )

    outliers.sort(key=lambda o: (o["assay"], o["sample_id"] or ""))

    return {
        "row_count": int(len(df)),
        "missing_values": missing_values,
        "per_assay": dict(sorted(per_assay.items())),
        "outliers": outliers,
    }
