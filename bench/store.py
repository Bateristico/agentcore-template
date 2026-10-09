"""Run results on disk (bench/out/<run_id>/) and in the results bucket (runs/<run_id>/) (spec §5 Outputs)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.records import Row
from template_core.config import REPO_ROOT

LOCAL_ROOT = REPO_ROOT / "bench" / "out"
_TYPES = {"run.json": "application/json", "results.jsonl": "application/x-ndjson", "report.html": "text/html; charset=utf-8"}


class RunStore:
    def __init__(self, local_root: Path = LOCAL_ROOT, s3: Any = None, bucket: str | None = None):
        self.local_root = Path(local_root)
        self.s3 = s3
        self.bucket = bucket

    def _write(self, run_id: str, files: dict[str, str]) -> list[str]:
        folder = self.local_root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        locations = []
        for name, content in files.items():
            (folder / name).write_text(content, encoding="utf-8")
            locations.append(str(folder / name))
            if self.s3 is not None and self.bucket:
                key = f"runs/{run_id}/{name}"
                self.s3.put_object(Bucket=self.bucket, Key=key, Body=content.encode("utf-8"), ContentType=_TYPES[name])
                locations.append(f"s3://{self.bucket}/{key}")
        return locations

    def save(self, meta: dict[str, Any], rows: list[Row], html: str) -> list[str]:
        results = "".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in rows)
        return self._write(
            meta["run_id"],
            {"run.json": json.dumps(meta, indent=2, ensure_ascii=False, default=str), "results.jsonl": results, "report.html": html},
        )

    def save_report(self, run_id: str, html: str) -> list[str]:
        return self._write(run_id, {"report.html": html})

    def _read(self, run_id: str, name: str) -> str:
        local = self.local_root / run_id / name
        if local.exists():
            return local.read_text(encoding="utf-8")
        if self.s3 is None or not self.bucket:
            raise FileNotFoundError(f"run {run_id} not found in {self.local_root}")
        return self.s3.get_object(Bucket=self.bucket, Key=f"runs/{run_id}/{name}")["Body"].read().decode("utf-8")

    def load(self, run_id: str) -> tuple[dict[str, Any], list[Row]]:
        meta = json.loads(self._read(run_id, "run.json"))
        rows = [Row.from_dict(json.loads(line)) for line in self._read(run_id, "results.jsonl").splitlines() if line.strip()]
        return meta, rows

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.s3 is not None and self.bucket:
            prefixes, token = [], None
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": "runs/", "Delimiter": "/"}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = self.s3.list_objects_v2(**kwargs)
                prefixes += [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
                if not resp.get("IsTruncated"):
                    break
                token = resp["NextContinuationToken"]
            metas = []
            for prefix in prefixes[-limit:]:
                body = self.s3.get_object(Bucket=self.bucket, Key=f"{prefix}run.json")["Body"].read()
                metas.append(json.loads(body))
        else:
            metas = [json.loads(p.read_text(encoding="utf-8")) for p in self.local_root.glob("*/run.json")]
        return sorted(metas, key=lambda m: m.get("created_at", ""), reverse=True)[:limit]
