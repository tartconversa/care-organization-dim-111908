"""口径发布：固定观察日、组织关系与重述依据，并处理资料撤回。

发布记录是不可变的；撤回只新增状态，不改写历史版本。
- 已固定的历史版本仍可取出，并附带当时的组织关系与重述依据。
- 当前对外口径不得继续使用已失效映射。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .errors import PublishError
from .facts import FactAllocation, LinkResult

FAMILIES = ("legal", "responsibility", "finance")


@dataclass
class Publication:
    key: str
    family: str
    perspective: str
    mode: str
    org_relationship: str
    observation_date: date
    as_of: datetime
    basis_version: str
    dataset_id: str
    label: str
    allocations: tuple[FactAllocation, ...]
    issues: tuple[dict, ...]
    notes: tuple[str, ...]
    month: str | None = None
    identity_digest: str = ""
    relation_digest: str = ""
    name_digest: str = ""
    status: str = "active"
    superseded_by: str | None = None
    withdrawn: list[dict] = field(default_factory=list)

    @property
    def digest(self) -> str:
        payload = {
            "key": self.key,
            "family": self.family,
            "perspective": self.perspective,
            "mode": self.mode,
            "org_relationship": self.org_relationship,
            "observation_date": self.observation_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "basis_version": self.basis_version,
            "dataset_id": self.dataset_id,
            "month": self.month,
            "identity_digest": self.identity_digest,
            "relation_digest": self.relation_digest,
            "allocations": [row.to_dict() for row in self.allocations],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def meta(self) -> dict[str, object]:
        return {
            "key": self.key,
            "family": self.family,
            "perspective": self.perspective,
            "mode": self.mode,
            "org_relationship": self.org_relationship,
            "observation_date": self.observation_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "basis_version": self.basis_version,
            "dataset_id": self.dataset_id,
            "label": self.label,
            "month": self.month,
            "identity_digest": self.identity_digest,
            "relation_digest": self.relation_digest,
            "name_digest": self.name_digest,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "digest": self.digest,
            "allocation_rows": len(self.allocations),
            "withdrawn": list(self.withdrawn),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.meta(),
            "issues": list(self.issues),
            "notes": list(self.notes),
            "allocations": [row.to_dict() for row in self.allocations],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Publication":
        allocations = tuple(_row_from_dict(row) for row in payload["allocations"])
        return cls(
            key=payload["key"],
            family=payload["family"],
            perspective=payload["perspective"],
            mode=payload["mode"],
            org_relationship=payload["org_relationship"],
            observation_date=date.fromisoformat(payload["observation_date"]),
            as_of=datetime.fromisoformat(payload["as_of"]),
            basis_version=payload["basis_version"],
            dataset_id=payload["dataset_id"],
            label=payload["label"],
            allocations=allocations,
            issues=tuple(payload.get("issues", [])),
            notes=tuple(payload.get("notes", [])),
            month=payload.get("month"),
            identity_digest=payload.get("identity_digest", ""),
            relation_digest=payload.get("relation_digest", ""),
            name_digest=payload.get("name_digest", ""),
            status=payload.get("status", "active"),
            superseded_by=payload.get("superseded_by"),
            withdrawn=list(payload.get("withdrawn", [])),
        )


def _row_from_dict(row: dict) -> FactAllocation:
    from decimal import Decimal

    return FactAllocation(
        service_id=row["service_id"],
        person_id=row["person_id"],
        site_id=row["site_id"],
        service=row["service"],
        occurred_on=date.fromisoformat(row["occurred_on"]),
        source_org=row["source_org"],
        amount_fen=row["amount_fen"],
        perspective=row["perspective"],
        mode=row["mode"],
        org_id=row["org_id"],
        share=Decimal(str(row["share"])),
        service_count=Decimal(str(row["service_count"])),
        amount=row["amount_fen_attributed"],
        status=row["status"],
        reason=row["reason"],
        source_claim_org=row.get("source_claim_org"),
        notices=tuple(row.get("notices", [])),
        handovers=tuple(row.get("handovers", [])),
        origin_assignment=row.get("origin_assignment"),
    )


class Publisher:
    def __init__(self, store_dir: Path | None = None) -> None:
        self.publications: dict[str, Publication] = {}
        self.family_current: dict[str, str] = {}
        self.store_dir = store_dir
        self.withdrawals: list[dict] = []
        if store_dir is not None and (store_dir / "index.json").is_file():
            self._load(store_dir)

    # -- 发布 ---------------------------------------------------------
    def publish(
        self,
        key: str,
        link: LinkResult,
        *,
        label: str,
        as_of: datetime,
        org_relationship: str | None = None,
        month: str | None = None,
        identity_digest: str = "",
        relation_digest: str = "",
        name_digest: str = "",
    ) -> Publication:
        if key in self.publications:
            raise PublishError("duplicate_key", f"版本 {key} 已存在；发布记录不可覆盖")
        if key.startswith("withdrawn:"):
            raise PublishError("reserved_key", "withdrawn: 前缀为撤回状态保留")
        if link.mode == "restated" and link.perspective != "responsibility":
            raise PublishError(
                "restated_not_supported",
                "只有服务责任视角有承接重述依据；法人/财务视角不能套用责任树",
            )
        if org_relationship is None:
            org_relationship = link.perspective
        if org_relationship not in FAMILIES:
            raise PublishError("relationship_invalid", f"未知组织关系: {org_relationship}")
        family = f"{org_relationship}:{link.mode}"
        publication = Publication(
            key=key,
            family=family,
            perspective=link.perspective,
            mode=link.mode,
            org_relationship=org_relationship,
            observation_date=link.observation_date,
            as_of=as_of,
            basis_version=link.basis_version,
            dataset_id=link.dataset_id,
            label=label,
            allocations=link.allocations,
            issues=tuple(issue.to_dict() for issue in link.issues),
            notes=link.notes,
            month=month,
            identity_digest=identity_digest,
            relation_digest=relation_digest,
            name_digest=name_digest,
        )
        # 月度版本是固定快照，不参与“当前版本”的自动替换；只有非月度发布
        # 才滚动当前指针。这样研究部门可以长期取回某个月的固定口径。
        if month is None:
            previous = self.family_current.get(family)
            if previous is not None and previous in self.publications:
                self.publications[previous].status = "superseded"
                self.publications[previous].superseded_by = key
            self.family_current[family] = key
        self.publications[key] = publication
        self._persist()
        return publication

    # -- 撤回 ---------------------------------------------------------
    def withdraw(
        self,
        *,
        withdrawal_id: str,
        notice_ids: tuple[str, ...] = (),
        handover_ids: tuple[str, ...] = (),
        org_ids: tuple[str, ...] = (),
        effective_at: datetime,
        reason: str,
    ) -> dict[str, object]:
        if not (notice_ids or handover_ids or org_ids):
            raise PublishError("withdraw_scope", "撤回必须指定通知、交接或机构范围")
        record = {
            "withdrawal_id": withdrawal_id,
            "notice_ids": list(notice_ids),
            "handover_ids": list(handover_ids),
            "org_ids": list(org_ids),
            "effective_at": effective_at.isoformat(),
            "reason": reason,
        }
        self.withdrawals.append(record)
        notices = set(notice_ids)
        handovers = set(handover_ids)
        orgs = set(org_ids)
        affected: list[dict[str, object]] = []
        for publication in self.publications.values():
            used_notices = {notice for row in publication.allocations for notice in row.notices}
            used_handovers = {item for row in publication.allocations for item in row.handovers}
            direct = bool(notices & used_notices) or bool(handovers & used_handovers)
            restated_basis = publication.mode == "restated"
            org_hit = bool(orgs) and (
                publication.org_relationship in orgs
                or any(row.org_id in orgs or row.source_org in orgs for row in publication.allocations)
            )
            if direct or org_hit:
                classification = "invalidated"
            elif restated_basis:
                classification = "needs_rebase"
            else:
                classification = "unaffected"
            if classification == "unaffected":
                continue
            if classification == "invalidated":
                if publication.status == "active":
                    publication.status = "withdrawn"
                publication.withdrawn.append(record)
            else:
                if publication.status == "active":
                    publication.status = "stale"
            affected.append(
                {
                    "key": publication.key,
                    "family": publication.family,
                    "dataset_id": publication.dataset_id,
                    "status": publication.status,
                    "classification": classification,
                    "used_notices": sorted(used_notices & notices) if notices else [],
                    "used_handovers": sorted(used_handovers & handovers) if handovers else [],
                }
            )
        invalidated_datasets = sorted(
            {item["dataset_id"] for item in affected if item["classification"] == "invalidated"}
        )
        current_invalidated = [
            family for family, key in self.family_current.items()
            if self.publications[key].status in ("withdrawn", "stale")
        ]
        self._persist()
        return {
            "withdrawal": record,
            "affected": affected,
            "invalidated_datasets": invalidated_datasets,
            "families_without_valid_current": sorted(current_invalidated),
        }

    # -- 读取 ---------------------------------------------------------
    def history(self, family: str | None = None) -> list[dict[str, object]]:
        items = [
            publication.meta()
            for publication in self.publications.values()
            if family is None or publication.family == family
        ]
        return sorted(items, key=lambda item: (item["observation_date"], item["key"]))

    def current(self, family: str) -> Publication:
        key = self.family_current.get(family)
        if key is None:
            raise PublishError(
                "no_current",
                f"口径 {family} 尚未发布，或仅有固定月度快照；请用 pinned(key) 取回",
            )
        publication = self.publications[key]
        if publication.status not in ("active", "stale"):
            raise PublishError(
                "current_invalid",
                f"口径 {family} 的当前版本 {key} 已因资料撤回失效，不得继续对外使用",
                status=publication.status,
            )
        if publication.status == "stale":
            # 仍指向当前版本，但提示需要按新依据重述
            pass
        return publication

    def pinned(self, key: str) -> Publication:
        publication = self.publications.get(key)
        if publication is None:
            raise PublishError("unknown_version", f"未知版本 {key}")
        return publication

    def publication(self, key: str) -> Publication:
        return self.pinned(key)

    def external_current(self, family: str) -> dict[str, object]:
        try:
            publication = self.current(family)
        except PublishError as exc:
            return {"family": family, "usable": False, "error": exc.to_dict()}
        return {
            "family": family,
            "usable": publication.status == "active",
            "key": publication.key,
            "status": publication.status,
            "dataset_id": publication.dataset_id,
            "org_relationship": publication.org_relationship,
            "observation_date": publication.observation_date.isoformat(),
            "basis_version": publication.basis_version,
            "warning": (
                "当前版本基于已撤回资料，需重述后才能对外使用"
                if publication.status != "active"
                else None
            ),
        }

    # -- 维表修订 -----------------------------------------------------
    def revision_impact(
        self,
        *,
        new_relation_digest: str,
        new_identity_digest: str,
        new_name_digest: str,
    ) -> dict[str, object]:
        """评估维表修订对已发布口径的影响。

        名称修订不等于主体或桥接关系修订：只改名称不使历史版本的
        组织关系失效，但必须记录名称已过期；桥接关系（上级边/站点归属）
        变化时，受影响口径必须重述，不能沿用旧桥接关系。
        """
        results = []
        for publication in self.publications.values():
            if publication.superseded_by is not None:
                continue
            if publication.status == "withdrawn":
                results.append(
                    {
                        "key": publication.key,
                        "classification": "withdrawn",
                        "status": publication.status,
                    }
                )
                continue
            if publication.relation_digest != new_relation_digest:
                classification = "relations_changed"
                if publication.status == "active":
                    publication.status = "stale"
                else:
                    publication.status = "stale"
            elif publication.identity_digest != new_identity_digest:
                classification = "identity_changed"
                if publication.status == "active":
                    publication.status = "stale"
            elif publication.name_digest != new_name_digest:
                classification = "name_only"
            else:
                classification = "unaffected"
            results.append(
                {
                    "key": publication.key,
                    "family": publication.family,
                    "status": publication.status,
                    "classification": classification,
                }
            )
        self._persist()
        return {
            "new_relation_digest": new_relation_digest,
            "new_identity_digest": new_identity_digest,
            "new_name_digest": new_name_digest,
            "publications": results,
            "rebase_required": sorted(
                item["key"]
                for item in results
                if item["classification"] in ("relations_changed", "identity_changed")
            ),
        }

    # -- 持久化 -------------------------------------------------------
    def _persist(self) -> None:
        if self.store_dir is None:
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        for publication in self.publications.values():
            path = self.store_dir / f"{publication.key}.json"
            path.write_text(
                json.dumps(publication.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        index = {
            "family_current": self.family_current,
            "withdrawals": self.withdrawals,
            "keys": sorted(self.publications),
            "dataset_ids": sorted({p.dataset_id for p in self.publications.values()}),
        }
        (self.store_dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self, store_dir: Path) -> None:
        index = json.loads((store_dir / "index.json").read_text(encoding="utf-8"))
        self.family_current = dict(index.get("family_current", {}))
        self.withdrawals = list(index.get("withdrawals", []))
        for key in index.get("keys", []):
            payload = json.loads((store_dir / f"{key}.json").read_text(encoding="utf-8"))
            self.publications[key] = Publication.from_dict(payload)


