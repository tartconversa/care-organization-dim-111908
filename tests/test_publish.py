"""发布、跨视角对照与撤回影响范围。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from careorg import Warehouse
from careorg.errors import PublishError
from tests.fixtures import (
    FIN_EDGES,
    LEGAL_EDGES,
    ORGS,
    RESP_EDGES,
    SITES,
    make_dataset,
)

TZ = timezone(timedelta(hours=8))


def _dataset(root: Path, *, successions, handovers, assignments, services) -> Warehouse:
    return Warehouse(
        make_dataset(
            root,
            organizations=ORGS,
            sites=SITES,
            hierarchies=LEGAL_EDGES + RESP_EDGES + FIN_EDGES,
            assignments=assignments,
            successions=successions,
            handovers=handovers,
            services=services,
        )
    )


def _assignments():
    rows = [
        {
            "assignment_id": "A1",
            "site_id": "S1",
            "service": "meal",
            "perspective": "responsibility",
            "org_id": "O2",
            "share": "1",
            "from_date": "2026-01-01",
            "until_date": "2026-11-01",
        },
        {
            "assignment_id": "AF1",
            "site_id": "S1",
            "service": "meal",
            "perspective": "finance",
            "org_id": "O3",
            "share": "1",
            "from_date": "2026-01-01",
            "until_date": None,
        },
    ]
    return rows


def _successions():
    return [
        {
            "notice_id": "N1",
            "from_org": "O2",
            "to_org": "O4",
            "sites": ["S1"],
            "services": ["meal"],
            "share": "0.6",
            "effective_date": "2026-11-01",
            "received_at": "2026-11-02T08:00:00+08:00",
        },
        {
            "notice_id": "N2",
            "from_org": "O2",
            "to_org": "O5",
            "sites": ["S1"],
            "services": ["meal"],
            "share": "0.4",
            "effective_date": "2026-11-01",
            "received_at": "2026-11-02T08:01:00+08:00",
        },
    ]


def _handovers():
    return [
        {
            "handover_id": "H1",
            "notice_id": "N1",
            "site_id": "S1",
            "service": "meal",
            "from_date": "2026-11-01",
            "until_date": None,
        },
        {
            "handover_id": "H2",
            "notice_id": "N2",
            "site_id": "S1",
            "service": "meal",
            "from_date": "2026-11-01",
            "until_date": None,
        },
    ]


def _services():
    return [
        {
            "service_id": "F1",
            "person_id": "P1",
            "site_id": "S1",
            "service": "meal",
            "occurred_on": "2026-10-01",
            "source_org": "O2",
            "amount_fen": 1000,
        },
        {
            "service_id": "F2",
            "person_id": "P1",
            "site_id": "S1",
            "service": "meal",
            "occurred_on": "2026-10-02",
            "source_org": "O2",
            "amount_fen": 1001,
        },
    ]


class PublicationLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.warehouse = _dataset(
            self.root,
            successions=_successions(),
            handovers=_handovers(),
            assignments=_assignments(),
            services=_services(),
        )
        self.recorded = self.warehouse.link(
            "responsibility", "as_recorded", date(2026, 10, 31), date(2026, 11, 20)
        )
        self.restated = self.warehouse.link(
            "responsibility", "restated", date(2026, 12, 1), date(2026, 12, 5)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_publish_records_relationship_and_basis(self) -> None:
        publication = self.warehouse.publish(
            "r1",
            self.restated,
            label="运营当前",
            as_of=datetime(2026, 12, 5, tzinfo=TZ),
        )
        self.assertEqual(publication.org_relationship, "responsibility")
        self.assertEqual(publication.mode, "restated")
        self.assertEqual(publication.observation_date, date(2026, 12, 1))
        self.assertEqual(publication.basis_version, self.restated.basis_version)
        self.assertEqual(publication.status, "active")

    def test_duplicate_key_rejected(self) -> None:
        self.warehouse.publish(
            "r1", self.restated, label="一版", as_of=datetime(2026, 12, 5, tzinfo=TZ)
        )
        with self.assertRaises(PublishError):
            self.warehouse.publish(
                "r1", self.restated, label="重复", as_of=datetime(2026, 12, 6, tzinfo=TZ)
            )

    def test_supersede_keeps_history(self) -> None:
        first = self.warehouse.publish(
            "r1", self.restated, label="月度", as_of=datetime(2026, 12, 5, tzinfo=TZ)
        )
        second = self.warehouse.publish(
            "r2", self.restated, label="修订", as_of=datetime(2026, 12, 6, tzinfo=TZ)
        )
        self.assertEqual(first.status, "superseded")
        self.assertEqual(first.superseded_by, "r2")
        self.assertEqual(second.status, "active")
        self.assertEqual(self.warehouse.publisher.current("responsibility:restated").key, "r2")
        # 固定版本仍可取回
        self.assertEqual(self.warehouse.publisher.pinned("r1").label, "月度")

    def test_restated_not_allowed_for_finance(self) -> None:
        finance = self.warehouse.link(
            "finance", "restated", date(2026, 12, 1), date(2026, 12, 5)
        )
        with self.assertRaises(PublishError):
            self.warehouse.publish(
                "f1", finance, label="错误", as_of=datetime(2026, 12, 5, tzinfo=TZ)
            )

    def test_compare_does_not_multiply_amount(self) -> None:
        left = self.warehouse.publish(
            "r1", self.recorded, label="研究月度", as_of=datetime(2026, 11, 20, tzinfo=TZ)
        )
        right = self.warehouse.publish(
            "r2", self.restated, label="运营当前", as_of=datetime(2026, 12, 5, tzinfo=TZ)
        )
        comparison = self.warehouse.query.compare(left, right)
        self.assertEqual(comparison["amount_mismatches"], 0)
        self.assertEqual(comparison["rows"][0]["left"]["amount_fen"], 1000)
        self.assertEqual(comparison["rows"][0]["right"]["amount_fen"], 1000)
        self.assertEqual(
            comparison["rows"][0]["left"]["share_total"],
            comparison["rows"][0]["right"]["share_total"],
        )

    def test_compare_with_org_filter_does_not_report_false_mismatch(self) -> None:
        left = self.warehouse.publish(
            "r1", self.recorded, label="研究月度", as_of=datetime(2026, 11, 20, tzinfo=TZ)
        )
        right = self.warehouse.publish(
            "r2", self.restated, label="运营当前", as_of=datetime(2026, 12, 5, tzinfo=TZ)
        )
        unfiltered = self.warehouse.query.compare(left, right)
        filtered = self.warehouse.query.compare(left, right, org_ids={"O4"})
        self.assertEqual(unfiltered["amount_mismatches"], 0)
        self.assertEqual(filtered["amount_mismatches"], 0)
        f1 = next(row for row in filtered["rows"] if row["service_id"] == "F1")
        self.assertEqual(f1["left"]["allocations"], [])
        self.assertEqual(f1["right"]["allocations"][0]["org_id"], "O4")
        self.assertTrue(f1["amount_consistent"])

    def test_compare_requires_same_dataset(self) -> None:
        other = Warehouse(
            make_dataset(
                Path(self.temp.name) / "other",
                organizations=ORGS,
                sites=SITES,
                hierarchies=[],
                assignments=[],
                successions=[],
                handovers=[],
                services=[dict(_services()[0], amount_fen=999)],
            )
        )
        other_link = other.link("legal", "as_recorded")
        left = self.warehouse.publish(
            "r1", self.recorded, label="A", as_of=datetime(2026, 11, 20, tzinfo=TZ)
        )
        right = other.publish(
            "x1", other_link, label="B", as_of=datetime(2026, 11, 20, tzinfo=TZ)
        )
        with self.assertRaises(Exception):
            other.query.compare(left, right)

    def test_withdrawal_invalidates_current_but_keeps_pinned(self) -> None:
        pinned = self.warehouse.publish(
            "r1", self.restated, label="月度快照", as_of=datetime(2026, 12, 5, tzinfo=TZ)
        )
        self.warehouse.publish(
            "r2", self.restated, label="当前", as_of=datetime(2026, 12, 6, tzinfo=TZ)
        )
        result = self.warehouse.publisher.withdraw(
            withdrawal_id="W1",
            notice_ids=("N1",),
            effective_at=datetime(2026, 12, 10, tzinfo=TZ),
            reason="通知撤回",
        )
        self.assertIn("r2", {item["key"] for item in result["affected"]})
        self.assertIn("r1", {item["key"] for item in result["affected"]})
        self.assertEqual(result["invalidated_datasets"], [self.warehouse.bundle.dataset_id])
        self.assertFalse(
            self.warehouse.publisher.external_current("responsibility:restated")["usable"]
        )
        # 历史固定版本仍可取出并解释当时依据
        stored = self.warehouse.publisher.pinned("r1")
        # r1 早已被 r2 取代；撤回不抹掉固定历史，只登记失效依据
        self.assertEqual(stored.status, "superseded")
        self.assertEqual([item["withdrawal_id"] for item in stored.withdrawn], ["W1"])
        self.assertEqual(stored.basis_version, pinned.basis_version)
        self.assertEqual(stored.observation_date, date(2026, 12, 1))
        self.assertEqual(stored.allocations, pinned.allocations)
        # 未使用被撤回通知的口径不受影响
        recorded = self.warehouse.publish(
            "a1", self.recorded, label="原归属", as_of=datetime(2026, 11, 20, tzinfo=TZ)
        )
        self.assertEqual(recorded.status, "active")

    def test_persistence_round_trip(self) -> None:
        store = Path(self.temp.name) / "store"
        warehouse = _dataset(
            Path(self.temp.name) / "data2",
            successions=_successions(),
            handovers=_handovers(),
            assignments=_assignments(),
            services=_services(),
        )
        warehouse.store_dir = store
        warehouse.publisher.store_dir = store
        link = warehouse.link(
            "responsibility", "restated", date(2026, 12, 1), date(2026, 12, 5)
        )
        warehouse.publish("r1", link, label="月度", as_of=datetime(2026, 12, 5, tzinfo=TZ))
        reloaded = Warehouse(warehouse.root, store_dir=store)
        publication = reloaded.publisher.pinned("r1")
        self.assertEqual(publication.label, "月度")
        self.assertEqual(publication.digest, warehouse.publisher.pinned("r1").digest)
        self.assertEqual(len(publication.allocations), len(link.allocations))


class MonthlySnapshots(unittest.TestCase):
    def test_monthly_versions_are_pinned_not_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = _dataset(
                Path(directory),
                successions=_successions(),
                handovers=_handovers(),
                assignments=_assignments(),
                services=_services(),
            )
            october = warehouse.link(
                "responsibility", "as_recorded", date(2026, 10, 31), date(2026, 10, 31)
            )
            november = warehouse.link(
                "responsibility", "as_recorded", date(2026, 11, 30), date(2026, 11, 30)
            )
            first = warehouse.publish(
                "research-2026-10",
                october,
                label="研究月度 2026-10",
                as_of=datetime(2026, 10, 31, 23, 59, tzinfo=TZ),
                month="2026-10",
            )
            second = warehouse.publish(
                "research-2026-11",
                november,
                label="研究月度 2026-11",
                as_of=datetime(2026, 11, 30, 23, 59, tzinfo=TZ),
                month="2026-11",
            )
            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "active")
            self.assertEqual(warehouse.publisher.pinned("research-2026-10").month, "2026-10")
            with self.assertRaises(PublishError):
                warehouse.publisher.current("responsibility:as_recorded")

    def test_operational_current_still_rolls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = _dataset(
                Path(directory),
                successions=_successions(),
                handovers=_handovers(),
                assignments=_assignments(),
                services=_services(),
            )
            link = warehouse.link(
                "responsibility", "restated", date(2026, 12, 1), date(2026, 12, 5)
            )
            first = warehouse.publish(
                "op-1", link, label="运营 1", as_of=datetime(2026, 12, 5, tzinfo=TZ)
            )
            second = warehouse.publish(
                "op-2", link, label="运营 2", as_of=datetime(2026, 12, 6, tzinfo=TZ)
            )
            self.assertEqual(first.status, "superseded")
            self.assertEqual(
                warehouse.publisher.current("responsibility:restated").key, "op-2"
            )


class InputNormalisation(unittest.TestCase):
    def test_datetime_observation_and_cutoff_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = _dataset(
                Path(directory),
                successions=_successions(),
                handovers=_handovers(),
                assignments=_assignments(),
                services=_services(),
            )
            result = warehouse.link(
                "responsibility",
                "restated",
                datetime(2026, 12, 1, 9, 0, tzinfo=TZ),
                datetime(2026, 12, 5, 9, 0, tzinfo=TZ),
            )
            self.assertEqual(result.observation_date, date(2026, 12, 1))
            self.assertEqual(result.as_of, date(2026, 12, 5))
            self.assertTrue(warehouse.linker.check_conservation(result)["ok"])


class RevisionImpact(unittest.TestCase):
    def test_bridge_change_requires_rebase_but_rename_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original = _dataset(
                base / "a",
                successions=[],
                handovers=[],
                assignments=_assignments(),
                services=_services(),
            )
            link = original.link("responsibility", "as_recorded")
            original.publish(
                "r1", link, label="初始", as_of=datetime(2026, 11, 20, tzinfo=TZ)
            )
            # 只改名称：不触发重述
            renamed_orgs = [dict(org) for org in ORGS]
            renamed_orgs[0]["name"] = "合成机构1（新名）"
            renamed_orgs[0]["revision"] = 2
            renamed = Warehouse(
                make_dataset(
                    base / "b",
                    organizations=renamed_orgs,
                    sites=SITES,
                    hierarchies=LEGAL_EDGES + RESP_EDGES + FIN_EDGES,
                    assignments=_assignments(),
                    successions=[],
                    handovers=[],
                    services=_services(),
                )
            )
            renamed.publisher = original.publisher
            impact = renamed.revision_impact()
            self.assertNotIn("r1", impact["rebase_required"])

            # 改桥接关系（站点归属变更）：必须重述
            changed_assignments = _assignments()
            changed_assignments[0]["org_id"] = "O3"
            changed = Warehouse(
                make_dataset(
                    base / "c",
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=LEGAL_EDGES + RESP_EDGES + FIN_EDGES,
                    assignments=changed_assignments,
                    successions=[],
                    handovers=[],
                    services=_services(),
                )
            )
            changed.publisher = original.publisher
            impact = changed.revision_impact()
            self.assertIn("r1", impact["rebase_required"])
            self.assertEqual(original.publisher.pinned("r1").status, "stale")

    def test_identity_split_requires_rebase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original = _dataset(
                base / "a",
                successions=[],
                handovers=[],
                assignments=_assignments(),
                services=_services(),
            )
            link = original.link("responsibility", "as_recorded")
            original.publish("r1", link, label="初始", as_of=datetime(2026, 11, 20, tzinfo=TZ))
            split_orgs = ORGS + [{"org_id": "O8", "name": "拆分机构", "revision": 1}]
            split = Warehouse(
                make_dataset(
                    base / "b",
                    organizations=split_orgs,
                    sites=SITES,
                    hierarchies=LEGAL_EDGES + RESP_EDGES + FIN_EDGES,
                    assignments=_assignments(),
                    successions=[],
                    handovers=[],
                    services=_services(),
                )
            )
            split.publisher = original.publisher
            impact = split.revision_impact()
            self.assertIn("r1", impact["rebase_required"])


if __name__ == "__main__":
    unittest.main()
