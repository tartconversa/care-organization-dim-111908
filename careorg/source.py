"""来源资料装载：指纹、字段、引用与类型化解析。

装载只负责把来源变成可信的记录；不在此处决定任何组织口径。
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .errors import SourceError
from .timeutil import parse_date, parse_instant, parse_optional_date

KINDS = ("organizations", "sites", "hierarchies", "assignments", "successions", "handovers", "services")
KIND_BY_PATH = {f"data/{kind}.{ 'csv' if kind == 'services' else 'json'}": kind for kind in KINDS}
PERSPECTIVES = ("legal", "responsibility", "finance")


@dataclass(frozen=True)
class Organization:
    org_id: str
    name: str
    revision: int


@dataclass(frozen=True)
class Site:
    site_id: str
    village: str


@dataclass(frozen=True)
class HierarchyEdge:
    edge_id: str
    perspective: str
    child: str
    parent: str
    from_date: date
    until_date: date | None


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    site_id: str
    service: str
    perspective: str
    org_id: str
    share: Decimal
    from_date: date
    until_date: date | None


@dataclass(frozen=True)
class Succession:
    notice_id: str
    from_org: str
    to_org: str
    sites: frozenset[str]
    services: frozenset[str]
    share: Decimal
    effective_date: date
    received_at: datetime

    @property
    def received_on(self) -> date:
        return self.received_at.date()


@dataclass(frozen=True)
class Handover:
    handover_id: str
    notice_id: str
    site_id: str
    service: str
    from_date: date
    until_date: date | None


@dataclass(frozen=True)
class ServiceFact:
    service_id: str
    person_id: str
    site_id: str
    service: str
    occurred_on: date
    source_org: str
    amount_fen: int


@dataclass
class SourceBundle:
    root: Path
    raw: dict[str, list[dict]]
    organizations: list[Organization]
    sites: list[Site]
    hierarchies: list[HierarchyEdge]
    assignments: list[Assignment]
    successions: list[Succession]
    handovers: list[Handover]
    services: list[ServiceFact]
    dataset_id: str
    catalog: dict


def _canonical_rows(rows: list[dict]) -> bytes:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload.encode("utf-8")


def _sha256_rows(rows: list[dict]) -> str:
    return hashlib.sha256(_canonical_rows(rows)).hexdigest()


def _parse_decimal(value: object, field: str, context: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SourceError("decimal_invalid", f"{context}: {field} 必须是十进制数", field=field)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SourceError("decimal_invalid", f"{context}: {field} 不是有效十进制数: {value!r}", field=field) from exc
    if not result.is_finite():
        raise SourceError("decimal_invalid", f"{context}: {field} 必须是有限数", field=field)
    if result < 0:
        raise SourceError("decimal_invalid", f"{context}: {field} 不能为负", field=field)
    return result


def _parse_int(value: object, field: str, context: str) -> int:
    if isinstance(value, bool) or value is None:
        raise SourceError("integer_invalid", f"{context}: {field} 必须是整数", field=field)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    raise SourceError("integer_invalid", f"{context}: {field} 必须是整数: {value!r}", field=field)


def _require_str(row: dict, field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or value == "":
        raise SourceError("field_missing", f"{context}: {field} 必须是非空字符串", field=field)
    return value


def _require_str_set(row: dict, field: str, context: str) -> frozenset[str]:
    value = row.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        # 空集合不表示“全部承接”：拒绝空范围，避免把空通知误读成通配。
        raise SourceError(
            "field_missing", f"{context}: {field} 必须是非空字符串数组", field=field
        )
    return frozenset(value)


def _check_interval(start: date, end: date | None, field: str, context: str) -> None:
    if end is not None and end <= start:
        raise SourceError(
            "interval_invalid",
            f"{context}: 有效区间必须左闭右开且结束晚于开始",
            field=field,
        )


def _load_rows(path: Path, kind: str) -> list[dict]:
    if kind == "services":
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    else:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError("source_format", f"{path.name}: JSON 无法解析") from exc
        if not isinstance(loaded, list):
            raise SourceError("source_format", f"{path.name}: 顶层必须是数组")
        rows = loaded
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("source_format", f"{path.name}: 每行必须是对象")
    return rows


def _verify_catalog(root: Path, catalog: dict) -> list[dict]:
    if catalog.get("synthetic") is not True:
        raise SourceError("not_synthetic", "缺少合成资料声明")
    files = catalog.get("files")
    if not isinstance(files, list) or not files:
        raise SourceError("catalog_invalid", "catalog.files 必须是非空数组")
    for entry in files:
        if entry.get("path") not in KIND_BY_PATH:
            raise SourceError("source_unknown", f"catalog 登记了未知来源: {entry.get('path')!r}")
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root):
            raise SourceError("source_escape", f"来源路径越界: {entry.get('path')!r}")
        if not path.is_file():
            raise SourceError("source_missing", f"来源文件不存在: {entry.get('path')}")
        raw = path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest = hashlib.sha256(normalized).hexdigest()
        if digest != entry.get("sha256"):
            raise SourceError("fingerprint_mismatch", f"来源指纹不符: {entry.get('path')}")
        rows = _load_rows(path, KIND_BY_PATH[entry["path"]])
        if len(rows) != entry.get("rows"):
            raise SourceError("row_count_mismatch", f"来源条数不符: {entry.get('path')}")
        for row in rows:
            if set(row) != set(entry.get("fields", [])):
                raise SourceError("field_mismatch", f"字段不符: {entry.get('path')}")
    return files


def _verify_references(root: Path, files: list[dict], raw: dict[str, list[dict]]) -> None:
    for entry in files:
        kind = KIND_BY_PATH[entry["path"]]
        for ref in entry.get("references", []):
            if ref.get("target") not in KIND_BY_PATH:
                raise SourceError(
                    "source_unknown", f"引用目标未知: {ref.get('target')!r}"
                )
            target_kind = KIND_BY_PATH[ref["target"]]
            target_rows = raw[target_kind]
            allowed = {str(row[ref["target_field"]]) for row in target_rows}
            for row in raw[kind]:
                value = row.get(ref["field"])
                if value is None or value == "":
                    continue
                values = value if isinstance(value, list) else [value]
                if any(str(item) not in allowed for item in values):
                    raise SourceError(
                        "reference_missing",
                        f"{entry['path']}: {ref['field']} 引用了不存在的 {ref['target']}",
                        field=ref["field"],
                    )


def _typed(raw: dict[str, list[dict]]) -> dict[str, list]:
    organizations = []
    seen_org_revisions: set[tuple[str, int]] = set()
    for row in raw["organizations"]:
        org = Organization(
            org_id=_require_str(row, "org_id", "organizations"),
            name=_require_str(row, "name", "organizations"),
            revision=_parse_int(row.get("revision"), "revision", "organizations"),
        )
        if org.revision < 0:
            raise SourceError("revision_invalid", "organizations: revision 不能为负")
        if (org.org_id, org.revision) in seen_org_revisions:
            raise SourceError("duplicate_revision", f"organizations: 重复修订 {org.org_id}/{org.revision}")
        seen_org_revisions.add((org.org_id, org.revision))
        organizations.append(org)

    sites = [
        Site(site_id=_require_str(row, "site_id", "sites"), village=_require_str(row, "village", "sites"))
        for row in raw["sites"]
    ]
    seen_sites: set[str] = set()
    for site in sites:
        if site.site_id in seen_sites:
            raise SourceError("duplicate_key", f"sites: 重复站点 {site.site_id}")
        seen_sites.add(site.site_id)

    hierarchies = []
    seen_edges: set[str] = set()
    for row in raw["hierarchies"]:
        edge = HierarchyEdge(
            edge_id=_require_str(row, "edge_id", "hierarchies"),
            perspective=_require_str(row, "perspective", "hierarchies"),
            child=_require_str(row, "child", "hierarchies"),
            parent=_require_str(row, "parent", "hierarchies"),
            from_date=parse_date(row.get("from_date"), "from_date"),
            until_date=parse_optional_date(row.get("until_date"), "until_date"),
        )
        if edge.perspective not in PERSPECTIVES:
            raise SourceError("perspective_invalid", f"hierarchies: 未知视角 {edge.perspective}")
        if edge.edge_id in seen_edges:
            raise SourceError("duplicate_key", f"hierarchies: 重复边 {edge.edge_id}")
        if edge.child == edge.parent:
            raise SourceError("self_parent", f"hierarchies: {edge.edge_id} 不能以自身为上级")
        _check_interval(edge.from_date, edge.until_date, "until_date", f"hierarchies/{edge.edge_id}")
        seen_edges.add(edge.edge_id)
        hierarchies.append(edge)

    assignments = []
    seen_assignments: set[str] = set()
    for row in raw["assignments"]:
        assignment = Assignment(
            assignment_id=_require_str(row, "assignment_id", "assignments"),
            site_id=_require_str(row, "site_id", "assignments"),
            service=_require_str(row, "service", "assignments"),
            perspective=_require_str(row, "perspective", "assignments"),
            org_id=_require_str(row, "org_id", "assignments"),
            share=_parse_decimal(row.get("share"), "share", "assignments"),
            from_date=parse_date(row.get("from_date"), "from_date"),
            until_date=parse_optional_date(row.get("until_date"), "until_date"),
        )
        if assignment.perspective not in PERSPECTIVES:
            raise SourceError("perspective_invalid", f"assignments: 未知视角 {assignment.perspective}")
        if assignment.assignment_id in seen_assignments:
            raise SourceError("duplicate_key", f"assignments: 重复关系 {assignment.assignment_id}")
        _check_interval(assignment.from_date, assignment.until_date, "until_date", f"assignments/{assignment.assignment_id}")
        seen_assignments.add(assignment.assignment_id)
        assignments.append(assignment)

    successions = []
    seen_notices: set[str] = set()
    for row in raw["successions"]:
        succession = Succession(
            notice_id=_require_str(row, "notice_id", "successions"),
            from_org=_require_str(row, "from_org", "successions"),
            to_org=_require_str(row, "to_org", "successions"),
            sites=_require_str_set(row, "sites", "successions"),
            services=_require_str_set(row, "services", "successions"),
            share=_parse_decimal(row.get("share"), "share", "successions"),
            effective_date=parse_date(row.get("effective_date"), "effective_date"),
            received_at=parse_instant(row.get("received_at"), "received_at"),
        )
        if succession.notice_id in seen_notices:
            raise SourceError("duplicate_key", f"successions: 重复通知 {succession.notice_id}")
        if succession.from_org == succession.to_org:
            raise SourceError("succession_self", f"successions: {succession.notice_id} 不能承接给自身")
        seen_notices.add(succession.notice_id)
        successions.append(succession)

    handovers = []
    seen_handovers: set[str] = set()
    for row in raw["handovers"]:
        handover = Handover(
            handover_id=_require_str(row, "handover_id", "handovers"),
            notice_id=_require_str(row, "notice_id", "handovers"),
            site_id=_require_str(row, "site_id", "handovers"),
            service=_require_str(row, "service", "handovers"),
            from_date=parse_date(row.get("from_date"), "from_date"),
            until_date=parse_optional_date(row.get("until_date"), "until_date"),
        )
        if handover.handover_id in seen_handovers:
            raise SourceError("duplicate_key", f"handovers: 重复交接 {handover.handover_id}")
        _check_interval(handover.from_date, handover.until_date, "until_date", f"handovers/{handover.handover_id}")
        seen_handovers.add(handover.handover_id)
        handovers.append(handover)

    services = []
    seen_services: set[str] = set()
    for row in raw["services"]:
        fact = ServiceFact(
            service_id=_require_str(row, "service_id", "services"),
            person_id=_require_str(row, "person_id", "services"),
            site_id=_require_str(row, "site_id", "services"),
            service=_require_str(row, "service", "services"),
            occurred_on=parse_date(row.get("occurred_on"), "occurred_on"),
            source_org=_require_str(row, "source_org", "services"),
            amount_fen=_parse_int(row.get("amount_fen"), "amount_fen", "services"),
        )
        if fact.amount_fen < 0:
            raise SourceError("amount_invalid", f"services/{fact.service_id}: amount_fen 不能为负")
        if fact.service_id in seen_services:
            raise SourceError("duplicate_key", f"services: 重复服务 {fact.service_id}")
        seen_services.add(fact.service_id)
        services.append(fact)

    return {
        "organizations": organizations,
        "sites": sites,
        "hierarchies": hierarchies,
        "assignments": assignments,
        "successions": successions,
        "handovers": handovers,
        "services": services,
    }


def load_source(root: str | Path) -> SourceBundle:
    root = Path(root).resolve()
    catalog_path = root / "data/catalog.json"
    if not catalog_path.is_file():
        raise SourceError("catalog_missing", "缺少 data/catalog.json")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceError("catalog_invalid", "catalog.json 无法解析") from exc
    files = _verify_catalog(root, catalog)
    raw = {kind: _load_rows(root / f"data/{kind}.{ 'csv' if kind == 'services' else 'json'}", kind) for kind in KINDS}
    _verify_references(root, files, raw)
    typed = _typed(raw)
    dataset_id = "facts-" + _sha256_rows(raw["services"])[:16]
    return SourceBundle(
        root=root,
        raw=raw,
        organizations=typed["organizations"],
        sites=typed["sites"],
        hierarchies=typed["hierarchies"],
        assignments=typed["assignments"],
        successions=typed["successions"],
        handovers=typed["handovers"],
        services=typed["services"],
        dataset_id=dataset_id,
        catalog=catalog,
    )
