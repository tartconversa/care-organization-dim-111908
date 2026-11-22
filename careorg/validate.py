"""维度接收：相对独立的三个视角、有效段、上级关系与分担份额。

校验只隔离出问题的视角/时段，不把一个树的结论套到另一个树上。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .source import Assignment, HierarchyEdge, SourceBundle

PERSPECTIVES = ("legal", "responsibility", "finance")
ATTRIBUTION_PERSPECTIVES = ("responsibility", "finance")


@dataclass(frozen=True)
class Issue:
    code: str
    perspective: str
    from_date: date | None
    until_date: date | None
    message: str
    severity: str = "error"
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "perspective": self.perspective,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "until_date": self.until_date.isoformat() if self.until_date else None,
            "message": self.message,
            "severity": self.severity,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class Claim:
    assignment_id: str
    org_id: str
    share: Decimal


@dataclass(frozen=True)
class AssignmentSegment:
    perspective: str
    site_id: str
    service: str
    start: date
    end: date | None
    claims: tuple[Claim, ...]
    total_share: Decimal
    status: str  # ok / over
    shortfall: Decimal
    issue_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "site_id": self.site_id,
            "service": self.service,
            "from_date": self.start.isoformat(),
            "until_date": self.end.isoformat() if self.end else None,
            "total_share": str(self.total_share),
            "shortfall": str(self.shortfall),
            "status": self.status,
            "claims": [
                {"assignment_id": c.assignment_id, "org_id": c.org_id, "share": str(c.share)}
                for c in self.claims
            ],
            "issue_codes": list(self.issue_codes),
        }


@dataclass(frozen=True)
class HierarchySegment:
    perspective: str
    start: date
    end: date | None
    edges: tuple[HierarchyEdge, ...]
    status: str  # ok / cycle / duplicate_parent
    cycle: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()

    def parents(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for edge in self.edges:
            result.setdefault(edge.child, []).append(edge.parent)
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "from_date": self.start.isoformat(),
            "until_date": self.end.isoformat() if self.end else None,
            "edge_ids": [e.edge_id for e in self.edges],
            "status": self.status,
            "cycle": list(self.cycle),
            "issue_codes": list(self.issue_codes),
        }


def _boundaries(starts: list[date], ends: list[date | None]) -> list[date]:
    points = set(starts)
    points.update(end for end in ends if end is not None)
    return sorted(points)


def _find_cycle(parents: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []
    position: dict[str, int] = {}

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        position[node] = len(stack)
        stack.append(node)
        for parent in parents.get(node, []):
            if state.get(parent, 0) == 0:
                found = visit(parent)
                if found:
                    return found
            elif state.get(parent) == 1:
                return stack[position[parent] :] + [parent]
        stack.pop()
        position.pop(node, None)
        state[node] = 2
        return None

    for node in list(parents):
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def _merge_adjacent(segments: list, signature) -> list:
    merged: list = []
    for segment in segments:
        if merged and signature(merged[-1]) == signature(segment):
            previous = merged[-1]
            merged[-1] = previous.__class__(
                **{**previous.__dict__, "end": segment.end}
            )
        else:
            merged.append(segment)
    return merged


def build_hierarchy_segments(
    edges: list[HierarchyEdge], perspective: str
) -> tuple[list[HierarchySegment], list[Issue]]:
    scoped = [edge for edge in edges if edge.perspective == perspective]
    if not scoped:
        return [], []
    points = _boundaries(
        [edge.from_date for edge in scoped],
        [edge.until_date for edge in scoped],
    )
    segments: list[HierarchySegment] = []
    issues: list[Issue] = []
    for index, start in enumerate(points):
        end = points[index + 1] if index + 1 < len(points) else None
        active = [
            edge
            for edge in scoped
            if edge.from_date <= start and (edge.until_date is None or edge.until_date > start)
        ]
        parents: dict[str, list[str]] = {}
        for edge in active:
            parents.setdefault(edge.child, []).append(edge.parent)
        duplicate_children = {child for child, values in parents.items() if len(set(values)) > 1}
        cycle = _find_cycle(parents)
        codes: list[str] = []
        status = "ok"
        if duplicate_children:
            codes.append("duplicate_parent")
            status = "duplicate_parent"
            issues.append(
                Issue(
                    "duplicate_parent",
                    perspective,
                    start,
                    end,
                    f"{perspective} 视角存在多个上级: {', '.join(sorted(duplicate_children))}",
                    refs=tuple(sorted(duplicate_children)),
                )
            )
        if cycle:
            codes.append("cycle")
            status = "cycle" if status == "ok" else f"{status}+cycle"
            issues.append(
                Issue(
                    "cycle",
                    perspective,
                    start,
                    end,
                    f"{perspective} 视角存在循环: {' -> '.join(cycle)}",
                    refs=tuple(cycle),
                )
            )
        segments.append(
            HierarchySegment(
                perspective=perspective,
                start=start,
                end=end,
                edges=tuple(active),
                status=status,
                cycle=tuple(cycle or ()),
                issue_codes=tuple(codes),
            )
        )
    merged = _merge_adjacent(
        segments,
        lambda segment: (
            tuple(edge.edge_id for edge in segment.edges),
            segment.status,
            segment.cycle,
            segment.issue_codes,
        ),
    )
    return merged, issues


def build_assignment_segments(
    assignments: list[Assignment], perspective: str
) -> dict[tuple[str, str], list[AssignmentSegment]]:
    grouped: dict[tuple[str, str], list[Assignment]] = {}
    for assignment in assignments:
        if assignment.perspective != perspective:
            continue
        grouped.setdefault((assignment.site_id, assignment.service), []).append(assignment)

    result: dict[tuple[str, str], list[AssignmentSegment]] = {}
    for key, scoped in grouped.items():
        points = _boundaries(
            [assignment.from_date for assignment in scoped],
            [assignment.until_date for assignment in scoped],
        )
        segments: list[AssignmentSegment] = []
        for index, start in enumerate(points):
            end = points[index + 1] if index + 1 < len(points) else None
            active = [
                assignment
                for assignment in scoped
                if assignment.from_date <= start
                and (assignment.until_date is None or assignment.until_date > start)
            ]
            if not active:
                continue
            claims = tuple(
                Claim(assignment.assignment_id, assignment.org_id, assignment.share)
                for assignment in active
            )
            total = sum((claim.share for claim in claims), Decimal(0))
            codes: list[str] = []
            status = "ok"
            if total > 1:
                status = "over"
                codes.append("share_over_allocated")
            org_counts: dict[str, int] = {}
            for claim in claims:
                org_counts[claim.org_id] = org_counts.get(claim.org_id, 0) + 1
            if any(count > 1 for count in org_counts.values()):
                codes.append("duplicate_org_claim")
            segments.append(
                AssignmentSegment(
                    perspective=perspective,
                    site_id=key[0],
                    service=key[1],
                    start=start,
                    end=end,
                    claims=claims,
                    total_share=total,
                    status=status,
                    shortfall=max(Decimal(0), Decimal(1) - total),
                    issue_codes=tuple(codes),
                )
            )
        result[key] = _merge_adjacent(
            segments,
            lambda segment: (
                tuple((claim.assignment_id, claim.org_id, claim.share) for claim in segment.claims),
                segment.status,
                segment.shortfall,
            ),
        )
    return result


def _segment_at(segments: list, day: date):
    for segment in segments:
        if segment.start <= day and (segment.end is None or day < segment.end):
            return segment
    return None


class ValidationIndex:
    """按视角缓存的校验索引；查询时只取指定视角的时段。"""

    def __init__(self, bundle: SourceBundle) -> None:
        self.bundle = bundle
        self.hierarchy: dict[str, list[HierarchySegment]] = {}
        self.hierarchy_issues: dict[str, list[Issue]] = {}
        for perspective in PERSPECTIVES:
            segments, issues = build_hierarchy_segments(bundle.hierarchies, perspective)
            self.hierarchy[perspective] = segments
            self.hierarchy_issues[perspective] = issues
        self.assignments: dict[str, dict[tuple[str, str], list[AssignmentSegment]]] = {}
        for perspective in ATTRIBUTION_PERSPECTIVES:
            self.assignments[perspective] = build_assignment_segments(
                bundle.assignments, perspective
            )
        self.assignment_issues: list[Issue] = self._assignment_issues()
        self._validate_links()

    def _assignment_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        for perspective, groups in self.assignments.items():
            for (site_id, service), segments in groups.items():
                for segment in segments:
                    if segment.status == "over":
                        issues.append(
                            Issue(
                                "share_over_allocated",
                                perspective,
                                segment.start,
                                segment.end,
                                f"{site_id}/{service} 份额合计 {segment.total_share} 超过 1，时段整体隔离",
                                refs=tuple(claim.assignment_id for claim in segment.claims),
                            )
                        )
                    elif segment.shortfall > 0:
                        issues.append(
                            Issue(
                                "share_shortfall",
                                perspective,
                                segment.start,
                                segment.end,
                                f"{site_id}/{service} 尚有 {segment.shortfall} 份额未归属，保留为未归属部分",
                                severity="notice",
                                refs=tuple(claim.assignment_id for claim in segment.claims),
                            )
                        )
                    if "duplicate_org_claim" in segment.issue_codes:
                        issues.append(
                            Issue(
                                "duplicate_org_claim",
                                perspective,
                                segment.start,
                                segment.end,
                                f"{site_id}/{service} 同一机构存在多条并行关系，份额按行累加",
                                severity="notice",
                                refs=tuple(claim.assignment_id for claim in segment.claims),
                            )
                        )
        for assignment in self.bundle.assignments:
            if assignment.perspective == "legal":
                issues.append(
                    Issue(
                        "legal_assignment_ignored",
                        "legal",
                        assignment.from_date,
                        assignment.until_date,
                        "法人视角不使用站点服务归属关系；该关系不参与服务责任或财务归集",
                        severity="notice",
                        refs=(assignment.assignment_id,),
                    )
                )
        return issues

    def _validate_links(self) -> None:
        problems: list[Issue] = []
        org_ids = {org.org_id for org in self.bundle.organizations}
        site_ids = {site.site_id for site in self.bundle.sites}
        notice_ids = {notice.notice_id for notice in self.bundle.successions}
        for handover in self.bundle.handovers:
            if handover.notice_id not in notice_ids:
                problems.append(
                    Issue(
                        "handover_notice_missing",
                        "responsibility",
                        handover.from_date,
                        handover.until_date,
                        f"交接 {handover.handover_id} 引用了不存在的通知",
                        refs=(handover.handover_id,),
                    )
                )
        for assignment in self.bundle.assignments:
            if assignment.org_id not in org_ids or assignment.site_id not in site_ids:
                problems.append(
                    Issue(
                        "assignment_reference_missing",
                        assignment.perspective,
                        assignment.from_date,
                        assignment.until_date,
                        f"关系 {assignment.assignment_id} 引用了不存在的机构或站点",
                        refs=(assignment.assignment_id,),
                    )
                )
        for notice in self.bundle.successions:
            if notice.from_org not in org_ids or notice.to_org not in org_ids:
                problems.append(
                    Issue(
                        "succession_reference_missing",
                        "responsibility",
                        notice.effective_date,
                        None,
                        f"通知 {notice.notice_id} 引用了不存在的机构",
                        refs=(notice.notice_id,),
                    )
                )
        self.link_issues = problems

    def hierarchy_at(self, perspective: str, day: date) -> HierarchySegment | None:
        if perspective not in self.hierarchy:
            raise KeyError(perspective)
        return _segment_at(self.hierarchy[perspective], day)

    def assignment_at(
        self, perspective: str, site_id: str, service: str, day: date
    ) -> AssignmentSegment | None:
        if perspective not in self.assignments:
            raise KeyError(perspective)
        return _segment_at(self.assignments[perspective].get((site_id, service), []), day)

    def issues_for(self, perspective: str) -> list[Issue]:
        return [
            issue
            for issue in self.hierarchy_issues.get(perspective, [])
            if issue.severity == "error"
        ] + [
            issue
            for issue in self.assignment_issues + self.link_issues
            if issue.perspective == perspective and issue.severity == "error"
        ]

    def all_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        for perspective in PERSPECTIVES:
            issues.extend(self.hierarchy_issues.get(perspective, []))
        issues.extend(self.assignment_issues)
        issues.extend(self.link_issues)
        return _dedupe(issues)

    def relation_digest(self) -> str:
        """绑定“桥接关系”的指纹：上级边与站点归属，不含名称。"""
        import hashlib
        import json

        payload = {
            "hierarchies": [
                [
                    edge.edge_id,
                    edge.perspective,
                    edge.child,
                    edge.parent,
                    edge.from_date.isoformat(),
                    edge.until_date.isoformat() if edge.until_date else None,
                ]
                for edge in self.bundle.hierarchies
            ],
            "assignments": [
                [
                    item.assignment_id,
                    item.site_id,
                    item.service,
                    item.perspective,
                    item.org_id,
                    str(item.share),
                    item.from_date.isoformat(),
                    item.until_date.isoformat() if item.until_date else None,
                ]
                for item in self.bundle.assignments
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def identity_digest(self) -> str:
        """法人身份指纹：只绑定 org_id 集合。

        revision 是名称修订号；换名不产生新的法人主体，因此不进入身份指纹。
        拆分主体会表现为 org_id 集合变化，届时才需要重述。
        """
        import hashlib
        import json

        payload = sorted(org.org_id for org in self.bundle.organizations)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def name_digest(self) -> str:
        import hashlib
        import json

        payload = sorted([org.org_id, org.name, org.revision] for org in self.bundle.organizations)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def report(self) -> dict[str, object]:
        perspectives: dict[str, object] = {}
        for perspective in PERSPECTIVES:
            perspectives[perspective] = {
                "hierarchy_segments": [s.to_dict() for s in self.hierarchy.get(perspective, [])],
                "assignment_segments": {
                    f"{site}/{service}": [s.to_dict() for s in segments]
                    for (site, service), segments in self.assignments.get(perspective, {}).items()
                },
            }
        issues = self.all_issues()
        return {
            "dataset_id": self.bundle.dataset_id,
            "source_rows": {key: len(value) for key, value in self.bundle.raw.items()},
            "perspectives": perspectives,
            "issues": [issue.to_dict() for issue in issues],
            "error_count": sum(1 for issue in issues if issue.severity == "error"),
            "notice_count": sum(1 for issue in issues if issue.severity == "notice"),
            "note": "份额不足保留未归属；份额超过1或层级成环只隔离对应视角时段。",
        }

    def ancestors(self, perspective: str, org_id: str, day: date) -> list[str]:
        """返回自下而上的上级链；时段隔离时抛出 AllocationError。"""
        from .errors import AllocationError

        segment = self.hierarchy_at(perspective, day)
        if segment is None:
            return []
        if segment.status != "ok":
            raise AllocationError(
                "hierarchy_quarantined",
                f"{perspective} 视角在 {day.isoformat()} 的上级关系不可用: {segment.status}",
                perspective=perspective,
                day=day.isoformat(),
                cycle=list(segment.cycle),
            )
        parents = segment.parents()
        chain: list[str] = []
        seen: set[str] = set()
        current = org_id
        while True:
            if current in seen:
                raise AllocationError(
                    "hierarchy_cycle",
                    f"{perspective} 视角上级链出现循环",
                    perspective=perspective,
                    day=day.isoformat(),
                    node=current,
                )
            seen.add(current)
            candidates = parents.get(current, [])
            if not candidates:
                return chain
            if len(set(candidates)) > 1:
                raise AllocationError(
                    "hierarchy_duplicate_parent",
                    f"{perspective} 视角 {current} 同时存在多个上级",
                    perspective=perspective,
                    day=day.isoformat(),
                    node=current,
                )
            parent = candidates[0]
            chain.append(parent)
            current = parent


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple] = set()
    result: list[Issue] = []
    for issue in issues:
        key = (
            issue.code,
            issue.perspective,
            issue.from_date,
            issue.until_date,
            issue.severity,
            issue.refs,
            issue.message,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
