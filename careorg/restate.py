"""承接与重述：只按可证明的承接范围把历史事实转入新组织口径。

规则：
- 通知只在生效日 <= 观察日、且收到时间 <= 信息截止时可用。
- 只有同一通知在站点/服务范围上有实际交接记录，并覆盖观察日时，
  该通知才可证明承接，才允许重述。
- 份额合计超过 1 视为争议，不做归一、不做平均分摊，整段保持未归属。
- 已声明但不可证明的份额、以及声明份额不足 1 的剩余，保留为未映射，
  并标注“尚未具备重述条件”。
- 承接可以跨期链式发生；路径成环时该分支隔离为未归属。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .source import Handover, Succession


@dataclass(frozen=True)
class RestatedClaim:
    org_id: str
    share: Decimal
    notice_path: tuple[str, ...]
    handover_basis: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "org_id": self.org_id,
            "share": str(self.share),
            "notice_path": list(self.notice_path),
            "handover_basis": list(self.handover_basis),
        }


@dataclass(frozen=True)
class PendingClaim:
    share: Decimal
    reason: str
    notice_path: tuple[str, ...] = ()
    # 该未映射份额所依据的消息/交接，即使它们尚未足以证明承接。
    # 撤回时需要据此判定“这条未就绪项是否已不再适用”。
    basis_notices: tuple[str, ...] = ()
    basis_handovers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "share": str(self.share),
            "reason": self.reason,
            "notice_path": list(self.notice_path),
            "basis_notices": list(self.basis_notices),
            "basis_handovers": list(self.basis_handovers),
        }


@dataclass(frozen=True)
class RestatementIssue:
    code: str
    message: str
    site_id: str
    service: str
    org_id: str
    notices: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "site_id": self.site_id,
            "service": self.service,
            "org_id": self.org_id,
            "notices": list(self.notices),
        }


@dataclass(frozen=True)
class RestateOutcome:
    allocations: tuple[RestatedClaim, ...]
    pending: tuple[PendingClaim, ...]
    issues: tuple[RestatementIssue, ...]
    used_notices: tuple[str, ...]
    used_handovers: tuple[str, ...]

    @property
    def allocated_share(self) -> Decimal:
        return sum((claim.share for claim in self.allocations), Decimal(0))

    @property
    def pending_share(self) -> Decimal:
        return sum((claim.share for claim in self.pending), Decimal(0))


class SuccessionEngine:
    """在给定观察日和信息截止下判断承接是否可证明。"""

    def __init__(
        self,
        successions: list[Succession],
        handovers: list[Handover],
        observation_date: date,
        as_of,
        max_depth: int = 32,
    ) -> None:
        self.observation_date = observation_date
        self.as_of = as_of  # date 或带时区 datetime
        self.max_depth = max_depth
        self.as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        self.successions = successions
        self.handovers = handovers
        self._by_key: dict[tuple[str, str, str], list[Succession]] = {}
        for notice in successions:
            for site in notice.sites:
                for service in notice.services:
                    self._by_key.setdefault((notice.from_org, site, service), []).append(notice)
        self._handover_index: dict[tuple[str, str, str], list[Handover]] = {}
        for handover in handovers:
            self._handover_index.setdefault(
                (handover.notice_id, handover.site_id, handover.service), []
            ).append(handover)

    def _handover_covers(self, handover: Handover) -> bool:
        if handover.from_date > self.observation_date:
            return False
        if handover.until_date is not None and self.observation_date >= handover.until_date:
            return False
        return True

    def provable_handovers(self, notice_id: str, site_id: str, service: str) -> list[Handover]:
        candidates = self._handover_index.get((notice_id, site_id, service), [])
        return [handover for handover in candidates if self._handover_covers(handover)]

    def applicable_notices(
        self, org_id: str, site_id: str, service: str
    ) -> tuple[list[Succession], list[Succession]]:
        """返回 (当前可用通知, 尚未可用通知)。

        未可用包括尚未生效、尚未收到或缺乏实际交接证明。
        """
        notices = self._by_key.get((org_id, site_id, service), [])
        applicable: list[Succession] = []
        blocked: list[Succession] = []
        for notice in notices:
            if notice.effective_date > self.observation_date:
                blocked.append(notice)
                continue
            if notice.received_at.date() > self.as_of_date:
                blocked.append(notice)
                continue
            applicable.append(notice)
        return applicable, blocked

    def readiness(self, notice: Succession, site_id: str, service: str) -> str:
        if notice.effective_date > self.observation_date:
            return "not_yet_effective"
        if notice.received_at.date() > self.as_of_date:
            return "not_received_by_as_of"
        if not self.provable_handovers(notice.notice_id, site_id, service):
            return "handover_pending"
        return "ready"

    def distribute(
        self,
        org_id: str,
        weight: Decimal,
        site_id: str,
        service: str,
        *,
        path_notices: tuple[str, ...] = (),
        path_orgs: tuple[str, ...] = (),
        path_handovers: tuple[str, ...] = (),
        depth: int = 0,
    ) -> RestateOutcome:
        allocations: list[RestatedClaim] = []
        pending: list[PendingClaim] = []
        issues: list[RestatementIssue] = []
        used_notices: list[str] = []
        used_handovers: list[str] = []

        if depth > self.max_depth:
            pending.append(
                PendingClaim(
                    weight,
                    "succession_depth_exceeded",
                    path_notices,
                    basis_notices=path_notices,
                    basis_handovers=path_handovers,
                )
            )
            issues.append(
                RestatementIssue(
                    "succession_depth_exceeded",
                    "承接链超过允许深度，相关份额保留为未归属",
                    site_id,
                    service,
                    org_id,
                    path_notices,
                )
            )
            return RestateOutcome((), tuple(pending), tuple(issues), (), ())

        applicable, blocked = self.applicable_notices(org_id, site_id, service)
        if not applicable:
            # 没有仍可适用的通知：该份额停在现机构，不属于未映射。
            return RestateOutcome(
                (RestatedClaim(org_id, weight, path_notices, path_handovers),),
                (),
                (),
                (),
                (),
            )

        declared_total = sum((notice.share for notice in applicable), Decimal(0))
        if declared_total > 1:
            notices = tuple(sorted(notice.notice_id for notice in applicable))
            pending.append(
                PendingClaim(
                    weight,
                    "disputed_succession_shares",
                    path_notices,
                    basis_notices=notices,
                )
            )
            issues.append(
                RestatementIssue(
                    "disputed_succession_shares",
                    f"承接份额合计 {declared_total} 超过 1；不归一、不平均，整段保留未归属",
                    site_id,
                    service,
                    org_id,
                    notices,
                )
            )
            return RestateOutcome((), tuple(pending), tuple(issues), notices, ())

        provable: list[Succession] = []
        for notice in applicable:
            handovers = self.provable_handovers(notice.notice_id, site_id, service)
            if handovers:
                provable.append(notice)
            else:
                used_notices.append(notice.notice_id)

        provable_share = sum((notice.share for notice in provable), Decimal(0))
        for notice in sorted(provable, key=lambda item: (item.effective_date, item.notice_id)):
            handovers = self.provable_handovers(notice.notice_id, site_id, service)
            handover_ids = tuple(sorted(handover.handover_id for handover in handovers))
            if notice.to_org in path_orgs or notice.to_org == org_id:
                pending.append(
                    PendingClaim(
                        weight * notice.share,
                        "succession_cycle",
                        path_notices + (notice.notice_id,),
                        basis_notices=path_notices + (notice.notice_id,),
                        basis_handovers=path_handovers + handover_ids,
                    )
                )
                issues.append(
                    RestatementIssue(
                        "succession_cycle",
                        f"承接链回到 {notice.to_org}，该分支保留为未归属",
                        site_id,
                        service,
                        org_id,
                        path_notices + (notice.notice_id,),
                    )
                )
                continue
            branch = self.distribute(
                notice.to_org,
                weight * notice.share,
                site_id,
                service,
                path_notices=path_notices + (notice.notice_id,),
                path_orgs=path_orgs + (org_id,),
                path_handovers=path_handovers + handover_ids,
                depth=depth + 1,
            )
            allocations.extend(branch.allocations)
            pending.extend(branch.pending)
            issues.extend(branch.issues)
            used_notices.extend(branch.used_notices)
            used_handovers.extend(branch.used_handovers)
            used_notices.append(notice.notice_id)
            used_handovers.extend(handover_ids)

        residual = weight - weight * provable_share
        if residual > 0:
            reason = "restatement_not_ready" if provable_share < declared_total else "succession_shortfall"
            pending.append(
                PendingClaim(
                    residual,
                    reason,
                    path_notices,
                    basis_notices=tuple(
                        sorted(
                            notice.notice_id
                            for notice in applicable
                            if notice not in provable
                        )
                    ),
                    basis_handovers=path_handovers,
                )
            )
            if reason == "restatement_not_ready":
                blocked_ids = tuple(
                    sorted(
                        notice.notice_id
                        for notice in applicable
                        if notice not in provable
                    )
                )
                issues.append(
                    RestatementIssue(
                        "restatement_not_ready",
                        "承接通知存在，但实际交接资料未覆盖该范围，保留未映射",
                        site_id,
                        service,
                        org_id,
                        blocked_ids,
                    )
                )

        return RestateOutcome(
            tuple(allocations),
            tuple(pending),
            tuple(issues),
            tuple(sorted(set(used_notices))),
            tuple(sorted(set(used_handovers))),
        )


def basis_version(
    successions: list[Succession],
    handovers: list[Handover],
    observation_date: date,
    as_of: date | None,
) -> str:
    payload = {
        "observation_date": observation_date.isoformat(),
        "as_of": as_of.isoformat() if as_of else None,
        "successions": sorted(
            (
                notice.notice_id,
                notice.from_org,
                notice.to_org,
                sorted(notice.sites),
                sorted(notice.services),
                str(notice.share),
                notice.effective_date.isoformat(),
                notice.received_at.isoformat(),
            )
            for notice in successions
        ),
        "handovers": sorted(
            (
                item.handover_id,
                item.notice_id,
                item.site_id,
                item.service,
                item.from_date.isoformat(),
                item.until_date.isoformat() if item.until_date else None,
            )
            for item in handovers
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "basis-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
