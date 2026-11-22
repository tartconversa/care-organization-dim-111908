"""临时合成资料构造工具。

测试不依赖仓库内固定数据的哈希，而是把改动后的行写入临时目录并重建
catalog，因此可以自由构造跨期改组、部分承接、争议份额和成环场景。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FIELDS = {
    "services": [
        "service_id",
        "person_id",
        "site_id",
        "service",
        "occurred_on",
        "source_org",
        "amount_fen",
    ],
    "organizations": ["org_id", "name", "revision"],
    "sites": ["site_id", "village"],
    "hierarchies": ["edge_id", "perspective", "child", "parent", "from_date", "until_date"],
    "assignments": [
        "assignment_id",
        "site_id",
        "service",
        "perspective",
        "org_id",
        "share",
        "from_date",
        "until_date",
    ],
    "successions": [
        "notice_id",
        "from_org",
        "to_org",
        "sites",
        "services",
        "share",
        "effective_date",
        "received_at",
    ],
    "handovers": ["handover_id", "notice_id", "site_id", "service", "from_date", "until_date"],
}


def _write(path: Path, rows: list[dict], kind: str) -> None:
    if path.suffix == ".csv":
        import csv

        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS[kind])
            writer.writeheader()
            writer.writerows(rows)
    else:
        path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def make_dataset(
    destination: Path,
    *,
    organizations: list[dict] | None = None,
    sites: list[dict] | None = None,
    hierarchies: list[dict] | None = None,
    assignments: list[dict] | None = None,
    successions: list[dict] | None = None,
    handovers: list[dict] | None = None,
    services: list[dict] | None = None,
) -> Path:
    destination = Path(destination)
    data = destination / "data"
    data.mkdir(parents=True, exist_ok=True)
    payloads = {
        "organizations": organizations,
        "sites": sites,
        "hierarchies": hierarchies,
        "assignments": assignments,
        "successions": successions,
        "handovers": handovers,
        "services": services,
    }
    catalog = {"synthetic": True, "files": []}
    for kind, rows in payloads.items():
        suffix = "csv" if kind == "services" else "json"
        path = data / f"{kind}.{suffix}"
        rows = rows if rows is not None else []
        _write(path, rows, kind)
        raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        entry = {
            "path": f"data/{kind}.{suffix}",
            "format": "csv" if suffix == "csv" else "json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows),
            "fields": FIELDS[kind],
            "references": [],
        }
        catalog["files"].append(entry)
    catalog_path = data / "catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def copy_repo_data(destination: Path) -> Path:
    destination = Path(destination)
    shutil.copytree(ROOT / "data", destination / "data")
    return destination


# 基础维度，供各测试复用 ------------------------------------------------
ORGS = [
    {"org_id": f"O{i}", "name": f"合成机构{i}", "revision": 1} for i in range(1, 8)
]
SITES = [
    {"site_id": f"S{i}", "village": f"V{(i + 1) // 2}"} for i in range(1, 9)
]
RESP_EDGES = [
    {
        "edge_id": f"responsibility-{i}",
        "perspective": "responsibility",
        "child": f"O{i}",
        "parent": "O1",
        "from_date": "2026-01-01",
        "until_date": None,
    }
    for i in range(2, 8)
]
LEGAL_EDGES = [
    {
        "edge_id": f"legal-{i}",
        "perspective": "legal",
        "child": f"O{i}",
        "parent": "O1",
        "from_date": "2026-01-01",
        "until_date": None,
    }
    for i in range(2, 8)
]
FIN_EDGES = [
    {
        "edge_id": f"finance-{i}",
        "perspective": "finance",
        "child": f"O{i}",
        "parent": "O1",
        "from_date": "2026-01-01",
        "until_date": None,
    }
    for i in range(2, 8)
]
