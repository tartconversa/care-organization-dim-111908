"""事实关联：把来源服务事实按选定视角与组织口径解释成可加的行。

每个事实在每个视角内满足：
- 份额之和恒为 1（已归属 + 未归属 + 隔离）；
- 金额之和恒等于来源金额（整数分，最大余数法，确定性）；
- 服务次数按确认份额加权，可加；
- 人数不做跨行相加，查询时按口径内人员去重。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .errors import AllocationError
from .restate import PendingClaim, RestatementIssue, RestatedClaim, SuccessionEngine, basis_version
from .source import ServiceFact, SourceBundle
from .timeutil import month_of
from .validate import ValidationIndex

MODES = ("as_recorded", "restated")


def _as_date(value) -> date:
    """接受 date 或带时区 datetime，统一取自然日。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"观察日/截止日必须是日期: {value!r}")
PENDING_REASONS = (
    "no_assignment",
    "share_shortfall",
    "restatement_not_ready",
    "handover_pending",
    "disputed_succession_shares",
    "succession_cycle",
    "succession_depth_exceeded",
    "allocation_over_one",
)
READY_REASONS = frozenset(
    {
        "restatement_not_ready",
        "handover_pending",
        "disputed_succession_shares",
        "succession_cycle",
        "succession_depth_exceeded",
    }
)


@dataclass(frozen=True)
class FactAllocation:
    service_id: str
    person_id: str
    site_id: str
    service: str
    occurred_on: date
    source_org: str
    amount_fen: int
    perspective: str
    mode: str
    org_id: str | None
    share: Decimal
    service_count: Decimal
    amount: int
    status: str
    reason: str | None = None
    source_claim_org: str | None = None
    notices: tuple[str, ...] = ()
    handovers: tuple[str, ...] = ()
    origin_assignment: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "service_id": self.service_id,
            "person_id": self.person_id,
            "site_id": self.site_id,
            "service": self.service,
            "occurred_on": self.occurred_on.isoformat(),
            "source_org": self.source_org,
            "amount_fen": self.amount_fen,
            "perspective": self.perspective,
            "mode": self.mode,
            "org_id": self.org_id,
            "share": decstr(self.share),
            "service_count": decstr(self.service_count),
            "amount_fen_attributed": self.amount,
            "status": self.status,
            "reason": self.reason,
            "source_claim_org": self.source_claim_org or self.org_id or self.source_org,
            "notices": list(self.notices),
            "handovers": list(self.handovers),
            "origin_assignment": self.origin_assignment,
        }


@dataclass(frozen=True)
class LinkResult:
    perspective: str
    mode: str
    observation_date: date
    as_of: date
    basis_version: str
    dataset_id: str
    allocations: tuple[FactAllocation, ...]
    issues: tuple[RestatementIssue, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "perspective": self.perspective,
            "mode": self.mode,
            "observation_date": self.observation_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "basis_version": self.basis_version,
            "dataset_id": self.dataset_id,
            "allocation_count": len(self.allocations),
            "issues": [issue.to_dict() for issue in self.issues],
            "notes": list(self.notes),
        }


def decstr(value: Decimal) -> str:
    """稳定的十进制字符串：去掉多余尾零，不用科学计数法。"""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")


def allocate_integer(total: int, weighted: list[tuple[object, Decimal]]) -> dict[object, int]:
    """把整数总额按权重分配到桶中，确定性最大余数法，保证总和不变。"""
    if total < 0:
        raise AllocationError("negative_amount", "金额不能为负")
    buckets = [(key, Decimal(weight)) for key, weight in weighted if weight > 0]
    if not buckets:
        if total == 0:
            return {}
        raise AllocationError("no_weight", "存在金额但没有可分配份额")
    floors: dict[object, int] = {}
    remainders: list[tuple[Decimal, str, object]] = []
    assigned = 0
    for key, weight in buckets:
        exact = Decimal(total) * weight
        floor = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
        floors[key] = floor
        assigned += floor
        remainders.append((exact - floor, repr(key), key))
    leftover = total - assigned
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for index in range(leftover):
        key = remainders[index % len(remainders)][2]
        floors[key] = floors.get(key, 0) + 1
    return floors


@dataclass(frozen=True)
class _Claim:
    org_id: str
    share: Decimal
    notices: tuple[str, ...] = ()
    handovers: tuple[str, ...] = ()
    assignment_id: str | None = None


class FactLinker:
    def __init__(self, bundle: SourceBundle, validation: ValidationIndex) -> None:
        self.bundle = bundle
        self.validation = validation

    # -- 视角基础归属 -------------------------------------------------
    def _base_claims(
        self, fact: ServiceFact, perspective: str
    ) -> tuple[list[_Claim], str | None]:
        """返回 (基础归属, 已归属份额不足时剩余部分的原因)。

        法人视角固定取来源法人；服务责任与财务视角取对应归属关系。
        两者互不代替。第二个返回值只描述“已归属但未满 1”的差额，
        完全没有归属关系时返回 no_assignment。
        """
        if perspective == "legal":
            return [_Claim(fact.source_org, Decimal(1))], None

        segment = self.validation.assignment_at(
            perspective, fact.site_id, fact.service, fact.occurred_on
        )
        if segment is None:
            return [], "no_assignment"
        if segment.status != "ok":
            return [], "share_over_allocated"
        # 份额不足 1 时，差额由 _materialize 的 residual 呈现，原因记为
        # share_shortfall；已归属部分仍可继续做承接重述。
        return [
            _Claim(claim.org_id, claim.share, assignment_id=claim.assignment_id)
            for claim in segment.claims
        ], ("share_shortfall" if segment.shortfall > 0 else None)

    def link(
        self,
        perspective: str,
        mode: str = "as_recorded",
        observation_date: date | None = None,
        as_of: date | None = None,
        *,
        facts: list[ServiceFact] | None = None,
        engine: SuccessionEngine | None = None,
    ) -> LinkResult:
        if perspective not in ("legal", "responsibility", "finance"):
            raise ValueError(f"未知视角: {perspective}")
        if mode not in MODES:
            raise ValueError(f"未知口径模式: {mode}")
        selected = facts if facts is not None else self.bundle.services
        if not selected:
            raise ValueError("没有可关联的服务事实")
        observation = (
            _as_date(observation_date)
            if observation_date is not None
            else max(fact.occurred_on for fact in selected)
        )
        cutoff = _as_date(as_of) if as_of is not None else observation
        notes: list[str] = []
        issues: list[RestatementIssue] = []
        seen_issues: set[tuple] = set()

        restating = mode == "restated" and perspective == "responsibility"
        if mode == "restated" and perspective != "responsibility":
            notes.append(
                f"{perspective} 视角没有承接重述依据；按原归属解释，"
                "法人/财务树不被责任树替代"
            )
        active_engine = None
        if restating:
            active_engine = engine or SuccessionEngine(
                self.bundle.successions, self.bundle.handovers, observation, cutoff
            )

        allocations: list[FactAllocation] = []
        for fact in selected:
            base, reason = self._base_claims(fact, perspective)
            pending: list[PendingClaim] = []
            if restating and reason is None:
                restated_claims, pending, restatement_issues = self._restate(base, fact, active_engine)
                for issue in restatement_issues:
                    key = (
                        issue.code,
                        issue.site_id,
                        issue.service,
                        issue.org_id,
                        issue.notices,
                    )
                    if key not in seen_issues:
                        seen_issues.add(key)
                        issues.append(issue)
                allocations.extend(
                    self._materialize(
                        fact,
                        perspective,
                        mode,
                        restated_claims,
                        pending,
                        None,
                    )
                )
            else:
                allocations.extend(
                    self._materialize(fact, perspective, mode, base, pending, reason)
                )

        if restating:
            basis = basis_version(
                self.bundle.successions,
                self.bundle.handovers,
                observation,
                cutoff,
            )
        elif perspective == "responsibility":
            basis = "as-recorded"
        else:
            basis = "not-applicable"
        return LinkResult(
            perspective=perspective,
            mode=mode,
            observation_date=observation,
            as_of=cutoff,
            basis_version=basis,
            dataset_id=self.bundle.dataset_id,
            allocations=tuple(allocations),
            issues=tuple(issues),
            notes=tuple(notes),
        )

    def _restate(
        self,
        base: list[_Claim],
        fact: ServiceFact,
        engine: SuccessionEngine | None,
    ) -> tuple[list[_Claim], list[PendingClaim], list[RestatementIssue]]:
        assert engine is not None
        claims: list[_Claim] = []
        pending: list[PendingClaim] = []
        issues: list[RestatementIssue] = []
        for claim in base:
            outcome = engine.distribute(claim.org_id, claim.share, fact.site_id, fact.service)
            for item in outcome.allocations:
                claims.append(self._from_restated(item, claim))
            pending.extend(outcome.pending)
            issues.extend(outcome.issues)
        return claims, pending, issues

    @staticmethod
    def _from_restated(item: RestatedClaim, base: _Claim) -> _Claim:
        notices = base.notices + item.notice_path
        handovers = base.handovers + item.handover_basis
        return _Claim(
            item.org_id,
            item.share,
            notices=tuple(sorted(set(notices))),
            handovers=tuple(sorted(set(handovers))),
            assignment_id=base.assignment_id,
        )

    def _materialize(
        self,
        fact: ServiceFact,
        perspective: str,
        mode: str,
        claims: list[_Claim],
        pending: list[PendingClaim],
        reason: str | None,
    ) -> list[FactAllocation]:
        if reason in ("share_over_allocated", "allocation_over_one"):
            return [self._quarantined(fact, perspective, mode, reason)]

        merged: dict[str, dict[str, object]] = {}
        for claim in claims:
            if claim.share <= 0:
                continue
            slot = merged.setdefault(
                claim.org_id,
                {"share": Decimal(0), "notices": set(), "handovers": set(), "assignment_id": claim.assignment_id},
            )
            slot["share"] = slot["share"] + claim.share
            slot["notices"].update(claim.notices)
            slot["handovers"].update(claim.handovers)

        mapped_share = sum((slot["share"] for slot in merged.values()), Decimal(0))
        pending_by_reason: dict[str, Decimal] = {}
        pending_basis: dict[str, tuple[set[str], set[str]]] = {}
        for item in pending:
            if item.share <= 0:
                continue
            pending_by_reason[item.reason] = pending_by_reason.get(item.reason, Decimal(0)) + item.share
            notices, handovers = pending_basis.setdefault(item.reason, (set(), set()))
            notices.update(item.basis_notices)
            handovers.update(item.basis_handovers)

        accounted = mapped_share + sum(pending_by_reason.values(), Decimal(0))
        if accounted > 1:
            return [self._quarantined(fact, perspective, mode, "allocation_over_one")]

        residual = Decimal(1) - accounted
        if residual > 0:
            fallback = reason or "no_assignment"
            pending_by_reason[fallback] = pending_by_reason.get(fallback, Decimal(0)) + residual

        weighted: list[tuple[object, Decimal]] = [
            (("org", org), slot["share"]) for org, slot in merged.items()
        ]
        weighted.extend((("pending", item), share) for item, share in pending_by_reason.items())
        buckets = allocate_integer(fact.amount_fen, weighted)

        rows: list[FactAllocation] = []
        for org in sorted(merged):
            slot = merged[org]
            rows.append(
                FactAllocation(
                    service_id=fact.service_id,
                    person_id=fact.person_id,
                    site_id=fact.site_id,
                    service=fact.service,
                    occurred_on=fact.occurred_on,
                    source_org=fact.source_org,
                    amount_fen=fact.amount_fen,
                    perspective=perspective,
                    mode=mode,
                    org_id=org,
                    share=slot["share"],
                    service_count=slot["share"],
                    amount=buckets.get(("org", org), 0),
                    status="mapped",
                    source_claim_org=fact.source_org,
                    notices=tuple(sorted(slot["notices"])),
                    handovers=tuple(sorted(slot["handovers"])),
                    origin_assignment=slot["assignment_id"],
                )
            )
        for pending_reason in sorted(pending_by_reason):
            share = pending_by_reason[pending_reason]
            status = "unmapped"
            if merged:
                status = "partially_unmapped"
            elif pending_reason in READY_REASONS:
                status = "not_ready"
            rows.append(
                FactAllocation(
                    service_id=fact.service_id,
                    person_id=fact.person_id,
                    site_id=fact.site_id,
                    service=fact.service,
                    occurred_on=fact.occurred_on,
                    source_org=fact.source_org,
                    amount_fen=fact.amount_fen,
                    perspective=perspective,
                    mode=mode,
                    org_id=None,
                    share=share,
                    service_count=share,
                    amount=buckets.get(("pending", pending_reason), 0),
                    status=status,
                    reason=pending_reason,
                    source_claim_org=fact.source_org,
                    notices=tuple(sorted(pending_basis.get(pending_reason, (set(), set()))[0])),
                    handovers=tuple(sorted(pending_basis.get(pending_reason, (set(), set()))[1])),
                )
            )
        return rows

    @staticmethod
    def _quarantined(
        fact: ServiceFact, perspective: str, mode: str, reason: str
    ) -> FactAllocation:
        return FactAllocation(
            service_id=fact.service_id,
            person_id=fact.person_id,
            site_id=fact.site_id,
            service=fact.service,
            occurred_on=fact.occurred_on,
            source_org=fact.source_org,
            amount_fen=fact.amount_fen,
            perspective=perspective,
            mode=mode,
            org_id=None,
            share=Decimal(1),
            service_count=Decimal(1),
            amount=fact.amount_fen,
            status="quarantined",
            reason=reason,
            source_claim_org=fact.source_org,
        )

    # -- 校验与守恒 ---------------------------------------------------
    def check_conservation(self, result: LinkResult) -> dict[str, object]:
        by_fact: dict[str, list[FactAllocation]] = {}
        for row in result.allocations:
            by_fact.setdefault(row.service_id, []).append(row)
        failures = []
        for service_id, rows in by_fact.items():
            share = sum((row.share for row in rows), Decimal(0))
            amount = sum(row.amount for row in rows)
            count = sum((row.service_count for row in rows), Decimal(0))
            expected_amount = rows[0].amount_fen
            if share != 1 or amount != expected_amount or count != 1:
                failures.append(
                    {
                        "service_id": service_id,
                        "share": decstr(share),
                        "amount": amount,
                        "expected_amount": expected_amount,
                        "service_count": decstr(count),
                    }
                )
        return {"ok": not failures, "failures": failures, "facts": len(by_fact)}

    def month_slice(self, month: str) -> list[ServiceFact]:
        return [fact for fact in self.bundle.services if month_of(fact.occurred_on) == month]
