"""仓储外观：装载、校验、关联、发布与查询的统一入口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .facts import FactLinker, LinkResult
from .publish import Publisher, Publication
from .query import FactQuery, QueryService
from .source import SourceBundle, load_source
from .validate import ValidationIndex


@dataclass
class Warehouse:
    root: str | Path = "."
    store_dir: Path | None = None
    bundle: SourceBundle | None = None
    validation: ValidationIndex | None = None
    linker: FactLinker | None = None
    publisher: Publisher | None = None
    query: QueryService | None = None

    def __post_init__(self) -> None:
        if self.bundle is None:
            self.bundle = load_source(self.root)
        if self.validation is None:
            self.validation = ValidationIndex(self.bundle)
        if self.linker is None:
            self.linker = FactLinker(self.bundle, self.validation)
        if self.query is None:
            self.query = QueryService(self.linker)
        if self.publisher is None:
            self.publisher = Publisher(self.store_dir)

    # -- 维度 ---------------------------------------------------------
    def dimensions(self) -> dict[str, object]:
        assert self.bundle is not None
        return {
            "dataset_id": self.bundle.dataset_id,
            "organizations": [
                {"org_id": org.org_id, "name": org.name, "revision": org.revision}
                for org in self.bundle.organizations
            ],
            "sites": [
                {"site_id": site.site_id, "village": site.village}
                for site in self.bundle.sites
            ],
            "perspectives": ["legal", "responsibility", "finance"],
            "note": "法人、服务责任树与财务汇总树相互独立，不能互相代替。",
        }

    def inspect(self) -> dict[str, object]:
        assert self.validation is not None
        return self.validation.report()

    def link(
        self,
        perspective: str,
        mode: str = "as_recorded",
        observation_date: date | None = None,
        as_of: date | None = None,
    ) -> LinkResult:
        assert self.linker is not None
        return self.linker.link(perspective, mode, observation_date, as_of)

    def publish(
        self,
        key: str,
        link: LinkResult,
        *,
        label: str,
        as_of: datetime,
        org_relationship: str | None = None,
        month: str | None = None,
    ) -> Publication:
        assert self.publisher is not None
        assert self.validation is not None
        return self.publisher.publish(
            key,
            link,
            label=label,
            as_of=as_of,
            org_relationship=org_relationship,
            month=month,
            identity_digest=self.validation.identity_digest(),
            relation_digest=self.validation.relation_digest(),
            name_digest=self.validation.name_digest(),
        )

    def revision_impact(self) -> dict[str, object]:
        """以当前装载的维表评估已发布口径是否需要重述。"""
        assert self.publisher is not None
        assert self.validation is not None
        return self.publisher.revision_impact(
            new_relation_digest=self.validation.relation_digest(),
            new_identity_digest=self.validation.identity_digest(),
            new_name_digest=self.validation.name_digest(),
        )

    def details(self, **kwargs) -> FactQuery:
        assert self.query is not None
        return self.query.details(**kwargs)
