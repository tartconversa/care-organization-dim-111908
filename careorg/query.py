"""多视角查询：明细、汇总与跨视角对照。

所有查询共享同一批来源服务事实；每个查询明确返回所选组织关系、
观察日期与重述依据。跨视角对照按 service_id 对齐，不把不同视角的
金额相加，避免同一笔金额倍增。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import CareOrgError
from .facts import FactAllocation, LinkResult, decstr
from .publish import Publication
from .timeutil import parse_month

GROUND_LEVELS = ("unit", "source", "site", "village")


@dataclass
class FactQuery:
    result: LinkResult
    rows: list[FactAllocation]
    site_villages: dict[str, str] | None = None

    @property
    def meta(self) -> dict[str, object]:
        return {
            "perspective": self.result.perspective,
            "mode": self.result.mode,
            "observation_date": self.result.observation_date.isoformat(),
            "as_of": self.result.as_of.isoformat(),
            "basis_version": self.result.basis_version,
            "dataset_id": self.result.dataset_id,
            "notes": list(self.result.notes),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": self.meta,
            "rows": [row.to_dict() for row in self.rows],
            "total_rows": len(self.rows),
        }

    def summary(self, level: str = "unit") -> dict[str, object]:
        return summarize(
            self.rows,
            level=level,
            meta=self.meta,
            site_villages=self.site_villages,
        )


def _group_key(
    row: FactAllocation, level: str, site_villages: dict[str, str] | None
) -> str:
    if level == "unit":
        return row.org_id or f"UNMAPPED:{row.reason or row.status}"
    if level == "source":
        return row.source_claim_org or row.source_org
    if level == "site":
        return row.site_id
    if level == "village":
        if site_villages and row.site_id in site_villages:
            return site_villages[row.site_id]
        return "UNKNOWN-VILLAGE"
    raise ValueError(f"未知汇总层级: {level}")


def summarize(
    rows: list[FactAllocation],
    *,
    level: str = "unit",
    meta: dict[str, object] | None = None,
    site_villages: dict[str, str] | None = None,
) -> dict[str, object]:
    if level not in GROUND_LEVELS:
        raise ValueError(f"未知汇总层级: {level}")
    groups: dict[str, dict[str, object]] = {}
    people: dict[str, set[str]] = {}
    for row in rows:
        key = _group_key(row, level, site_villages)
        slot = groups.setdefault(
            key,
            {
                "service_count": Decimal(0),
                "amount_fen": 0,
                "share": Decimal(0),
                "statuses": set(),
                "service_ids": set(),
                "mapped_share": Decimal(0),
                "unmapped_share": Decimal(0),
                "quarantined_share": Decimal(0),
            },
        )
        slot["service_count"] += row.service_count
        slot["amount_fen"] += row.amount
        slot["share"] += row.share
        slot["statuses"].add(row.status)
        slot["service_ids"].add(row.service_id)
        if row.status == "mapped":
            slot["mapped_share"] += row.share
        elif row.status == "quarantined":
            slot["quarantined_share"] += row.share
        else:
            slot["unmapped_share"] += row.share
        people.setdefault(key, set()).add(row.person_id)

    rows_out = []
    for key in sorted(groups):
        slot = groups[key]
        rows_out.append(
            {
                "key": key,
                "service_count": decstr(slot["service_count"]),
                "amount_fen": slot["amount_fen"],
                "share": decstr(slot["share"]),
                "mapped_share": decstr(slot["mapped_share"]),
                "unmapped_share": decstr(slot["unmapped_share"]),
                "quarantined_share": decstr(slot["quarantined_share"]),
                "person_count": len(people[key]),
                "statuses": sorted(slot["statuses"]),
                "distinct_services": len(slot["service_ids"]),
            }
        )

    total_share = sum((row.share for row in rows), Decimal(0))
    return {
        "meta": meta or {},
        "level": level,
        "groups": rows_out,
        "totals": {
            "service_count": decstr(sum((row.service_count for row in rows), Decimal(0))),
            "amount_fen": sum(row.amount for row in rows),
            "share": decstr(total_share),
            "mapped_share": decstr(
                sum((row.share for row in rows if row.status == "mapped"), Decimal(0))
            ),
            "unmapped_share": decstr(
                sum(
                    (row.share for row in rows if row.status.startswith("partially") or row.status == "unmapped" or row.status == "not_ready"),
                    Decimal(0),
                )
            ),
            "quarantined_share": decstr(
                sum((row.share for row in rows if row.status == "quarantined"), Decimal(0))
            ),
            "person_count_in_scope": len({row.person_id for row in rows}),
        },
        "note": "人数按口径内人员去重，不可跨组织简单相加；金额与次数按确认份额分担。",
    }


class QueryService:
    """在来源事实与发布版本之上提供查询，不重新解释份额。"""

    def __init__(self, linker) -> None:
        self.linker = linker
        self.site_villages = {
            site.site_id: site.village for site in linker.bundle.sites
        }

    def details(
        self,
        *,
        perspective: str,
        mode: str = "as_recorded",
        observation_date=None,
        as_of=None,
        org_id: str | None = None,
        source_org: str | None = None,
        site_id: str | None = None,
        service: str | None = None,
        month: str | None = None,
        include_unmapped: bool = True,
    ) -> FactQuery:
        facts = self.linker.month_slice(month) if month is not None else None
        if facts is not None and not facts:
            # 空月份是合法查询：返回零行，观察日回落到该月月末。
            _, month_end = parse_month(month)
            observation = observation_date or month_end
            cutoff = as_of or observation
            empty = LinkResult(
                perspective=perspective,
                mode=mode,
                observation_date=observation,
                as_of=cutoff,
                basis_version=(
                    "no-facts"
                    if mode == "restated"
                    else ("as-recorded" if perspective == "responsibility" else "not-applicable")
                ),
                dataset_id=self.linker.bundle.dataset_id,
                allocations=(),
                notes=(f"{month} 没有来源服务事实",),
            )
            return FactQuery(empty, [], self.site_villages)
        result = self.linker.link(
            perspective,
            mode,
            observation_date,
            as_of,
            facts=facts,
        )
        rows = list(result.allocations)
        if org_id is not None:
            rows = [row for row in rows if row.org_id == org_id]
        if source_org is not None:
            rows = [
                row
                for row in rows
                if row.source_claim_org == source_org or row.source_org == source_org
            ]
        if site_id is not None:
            rows = [row for row in rows if row.site_id == site_id]
        if service is not None:
            rows = [row for row in rows if row.service == service]
        if not include_unmapped:
            rows = [row for row in rows if row.org_id is not None]
        return FactQuery(result, rows, self.site_villages)

    def from_publication(self, publication: Publication) -> FactQuery:
        result = LinkResult(
            perspective=publication.perspective,
            mode=publication.mode,
            observation_date=publication.observation_date,
            as_of=publication.as_of.date(),
            basis_version=publication.basis_version,
            dataset_id=publication.dataset_id,
            allocations=publication.allocations,
            notes=publication.notes,
        )
        return FactQuery(result, list(publication.allocations), self.site_villages)

    def compare(
        self,
        left: Publication,
        right: Publication,
        *,
        org_ids: set[str] | None = None,
    ) -> dict[str, object]:
        """跨视角对照：按 service_id 对齐；金额两侧分别列示，绝不相加。"""
        if left.dataset_id != right.dataset_id:
            raise CareOrgError(
                "dataset_mismatch",
                "跨视角对照必须共享同一批来源服务事实",
                left=left.dataset_id,
                right=right.dataset_id,
            )
        left_by_fact = _index(left.allocations, org_ids)
        right_by_fact = _index(right.allocations, org_ids)
        # 金额一致性按未过滤的完整事实判定；org 过滤只影响展示，不应被误报
        # 成“口径不一致”。同一 service_id 两侧的完整金额必然相等。
        left_full = _index(left.allocations, None)
        right_full = _index(right.allocations, None)
        service_ids = sorted(set(left_by_fact) | set(right_by_fact))
        rows = []
        mismatches = 0
        for service_id in service_ids:
            left_rows = left_by_fact.get(service_id, [])
            right_rows = right_by_fact.get(service_id, [])
            full_left = sum(row.amount for row in left_full.get(service_id, []))
            full_right = sum(row.amount for row in right_full.get(service_id, []))
            if full_left != full_right:
                mismatches += 1
            rows.append(
                {
                    "service_id": service_id,
                    "left": _side(left, left_rows),
                    "right": _side(right, right_rows),
                    "amount_consistent": full_left == full_right,
                }
            )
        return {
            "left": {
                "key": left.key,
                "perspective": left.perspective,
                "mode": left.mode,
                "observation_date": left.observation_date.isoformat(),
                "basis_version": left.basis_version,
            },
            "right": {
                "key": right.key,
                "perspective": right.perspective,
                "mode": right.mode,
                "observation_date": right.observation_date.isoformat(),
                "basis_version": right.basis_version,
            },
            "dataset_id": left.dataset_id,
            "rows": rows,
            "amount_mismatches": mismatches,
            "note": (
                "两侧金额分别列示，不跨视角相加；amount_consistent 按完整事实判定。"
                "org 过滤只影响展示，不改变一致性结论。"
            ),
        }


def _index(
    allocations: tuple[FactAllocation, ...], org_ids: set[str] | None
) -> dict[str, list[FactAllocation]]:
    result: dict[str, list[FactAllocation]] = {}
    for row in allocations:
        if org_ids is not None and row.org_id not in org_ids:
            continue
        result.setdefault(row.service_id, []).append(row)
    return result


def _side(publication: Publication, rows: list[FactAllocation]) -> dict[str, object]:
    return {
        "key": publication.key,
        "allocations": [
            {
                "org_id": row.org_id,
                "share": decstr(row.share),
                "amount_fen": row.amount,
                "status": row.status,
            }
            for row in rows
        ],
        "share_total": decstr(sum((row.share for row in rows), Decimal(0))),
        "amount_fen": sum(row.amount for row in rows),
    }
