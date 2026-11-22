"""跨期改组与部分承接的组合过程核对。

场景：
- S1/S2 的 meal 服务在 2026-11-01 由 O2 改组给 O4（60%）与 O5（40%）。
- 交接资料只覆盖 S1 的 meal（H1），S2 尚未交接。
- S3 不受改组影响，仍由 O2 承担。
- 观察日 2026-12-01、信息截止 2026-12-05。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from careorg import Warehouse
from careorg.errors import AllocationError
from tests.fixtures import (
    FIN_EDGES,
    LEGAL_EDGES,
    ORGS,
    RESP_EDGES,
    SITES,
    copy_repo_data,
    make_dataset,
)


def _assignments():
    rows = []
    for index in range(1, 9):
        rows.append(
            {
                "assignment_id": f"A{index}",
                "site_id": f"S{index}",
                "service": "meal",
                "perspective": "responsibility",
                "org_id": "O2",
                "share": "1",
                "from_date": "2026-01-01",
                "until_date": "2026-11-01",
            }
        )
    rows.append(
        {
            "assignment_id": "AF1",
            "site_id": "S1",
            "service": "meal",
            "perspective": "finance",
            "org_id": "O3",
            "share": "1",
            "from_date": "2026-01-01",
            "until_date": None,
        }
    )
    return rows


def _services():
    return [
        {
            "service_id": f"F{index}",
            "person_id": f"P{index}",
            "site_id": f"S{index}",
            "service": "meal",
            "occurred_on": f"2026-10-{index:02d}",
            "source_org": "O2",
            "amount_fen": 900 + index,
        }
        for index in range(1, 9)
    ]


def _base(root: Path) -> Warehouse:
    return Warehouse(
        make_dataset(
            root,
            organizations=ORGS,
            sites=SITES,
            hierarchies=LEGAL_EDGES + RESP_EDGES + FIN_EDGES,
            assignments=_assignments(),
            successions=[
                {
                    "notice_id": "N1",
                    "from_org": "O2",
                    "to_org": "O4",
                    "sites": ["S1", "S2"],
                    "services": ["meal"],
                    "share": "0.6",
                    "effective_date": "2026-11-01",
                    "received_at": "2026-11-02T08:00:00+08:00",
                },
                {
                    "notice_id": "N2",
                    "from_org": "O2",
                    "to_org": "O5",
                    "sites": ["S1", "S2"],
                    "services": ["meal"],
                    "share": "0.4",
                    "effective_date": "2026-11-01",
                    "received_at": "2026-11-02T08:01:00+08:00",
                },
            ],
            handovers=[
                {
                    "handover_id": "H1",
                    "notice_id": "N1",
                    "site_id": "S1",
                    "service": "meal",
                    "from_date": "2026-11-01",
                    "until_date": None,
                }
            ],
            services=_services(),
        )
    )


class CrossPeriodRestatement(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.warehouse = _base(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _rows(self, mode: str, observation="2026-12-01", as_of="2026-12-05"):
        result = self.warehouse.link(
            "responsibility", mode, date.fromisoformat(observation), date.fromisoformat(as_of)
        )
        self.assertTrue(
            self.warehouse.linker.check_conservation(result)["ok"],
            self.warehouse.linker.check_conservation(result),
        )
        return result

    def test_partial_handover_only_restates_covered_site(self) -> None:
        result = self._rows("restated")
        by_fact = {}
        for row in result.allocations:
            by_fact.setdefault(row.service_id, []).append(row)

        s1 = by_fact["F1"]  # S1，有交接
        o4 = [row for row in s1 if row.org_id == "O4"]
        pending = [row for row in s1 if row.org_id is None]
        self.assertEqual(o4[0].share, Decimal("0.6"))
        self.assertEqual(o4[0].notices, ("N1",))
        self.assertEqual(o4[0].handovers, ("H1",))
        self.assertEqual(sum(row.share for row in pending), Decimal("0.4"))
        self.assertEqual(pending[0].status, "partially_unmapped")
        self.assertEqual(pending[0].reason, "restatement_not_ready")
        self.assertEqual(sum(row.amount for row in s1), 901)

        s2 = by_fact["F2"]  # S2，无交接
        self.assertEqual(len(s2), 1)
        self.assertIsNone(s2[0].org_id)
        self.assertEqual(s2[0].share, Decimal(1))
        self.assertEqual(s2[0].status, "not_ready")

    def test_unaffected_site_keeps_original_unit(self) -> None:
        result = self._rows("restated")
        f3 = [row for row in result.allocations if row.service_id == "F3"]
        self.assertEqual(len(f3), 1)
        self.assertEqual(f3[0].org_id, "O2")
        self.assertEqual(f3[0].status, "mapped")

    def test_no_average_split_of_unproven_share(self) -> None:
        """S2 的 1.0 不能按 60/40 摊给 O4/O5，必须整体保留未归属。"""
        result = self._rows("restated")
        f2 = [row for row in result.allocations if row.service_id == "F2"]
        self.assertEqual({row.org_id for row in f2}, {None})
        self.assertEqual(sum(row.share for row in f2), Decimal(1))

    def test_as_recorded_and_restated_share_same_facts(self) -> None:
        recorded = self._rows("as_recorded")
        restated = self._rows("restated")
        self.assertEqual(
            {row.service_id for row in recorded.allocations},
            {row.service_id for row in restated.allocations},
        )
        self.assertEqual(
            sum(row.amount for row in recorded.allocations),
            sum(row.amount for row in restated.allocations),
        )

    def test_restatement_waits_until_received(self) -> None:
        before = self.warehouse.link(
            "responsibility", "restated", date(2026, 12, 1), date(2026, 11, 1)
        )
        f1 = [row for row in before.allocations if row.service_id == "F1"]
        self.assertEqual(len(f1), 1)
        self.assertEqual(f1[0].org_id, "O2")
        self.assertEqual(f1[0].status, "mapped")

    def test_restatement_not_effective_yet(self) -> None:
        early = self.warehouse.link(
            "responsibility", "restated", date(2026, 10, 15), date(2026, 12, 5)
        )
        self.assertTrue(all(row.org_id == "O2" for row in early.allocations if row.perspective == "responsibility"))


class DisputedShares(unittest.TestCase):
    def test_conflicting_shares_are_not_normalised(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warehouse = Warehouse(
                make_dataset(
                    root,
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=RESP_EDGES,
                    assignments=[
                        {
                            "assignment_id": "A1",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O2",
                            "share": "0.7",
                            "from_date": "2026-01-01",
                            "until_date": None,
                        },
                        {
                            "assignment_id": "A2",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O3",
                            "share": "0.5",
                            "from_date": "2026-01-01",
                            "until_date": None,
                        },
                    ],
                    successions=[],
                    handovers=[],
                    services=[
                        {
                            "service_id": "F1",
                            "person_id": "P1",
                            "site_id": "S1",
                            "service": "meal",
                            "occurred_on": "2026-10-01",
                            "source_org": "O2",
                            "amount_fen": 1000,
                        }
                    ],
                )
            )
            report = warehouse.inspect()
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("share_over_allocated", codes)
            result = warehouse.link("responsibility", "as_recorded")
            self.assertEqual(len(result.allocations), 1)
            self.assertEqual(result.allocations[0].status, "quarantined")
            self.assertEqual(result.allocations[0].share, Decimal(1))
            self.assertEqual(result.allocations[0].amount, 1000)

    def test_shortfall_is_kept_unmapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=RESP_EDGES,
                    assignments=[
                        {
                            "assignment_id": "A1",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O2",
                            "share": "0.65",
                            "from_date": "2026-01-01",
                            "until_date": None,
                        }
                    ],
                    successions=[],
                    handovers=[],
                    services=[
                        {
                            "service_id": "F1",
                            "person_id": "P1",
                            "site_id": "S1",
                            "service": "meal",
                            "occurred_on": "2026-10-01",
                            "source_org": "O2",
                            "amount_fen": 1000,
                        }
                    ],
                )
            )
            result = warehouse.link("responsibility", "as_recorded")
            mapped = [row for row in result.allocations if row.org_id == "O2"]
            unmapped = [row for row in result.allocations if row.org_id is None]
            self.assertEqual(mapped[0].share, Decimal("0.65"))
            self.assertEqual(mapped[0].amount, 650)
            self.assertEqual(unmapped[0].share, Decimal("0.35"))
            self.assertEqual(unmapped[0].amount, 350)
            self.assertEqual(unmapped[0].reason, "share_shortfall")


class CycleIsolation(unittest.TestCase):
    def test_cycle_isolates_only_its_perspective_segment(self) -> None:
        edges = list(RESP_EDGES)
        # 2026-06-01 起服务责任树出现 O2 <-> O3 环，法人树不受影响
        edges = [
            edge
            for edge in edges
            if edge["edge_id"] not in ("responsibility-3",)
        ]
        edges.append(
            {
                "edge_id": "responsibility-3",
                "perspective": "responsibility",
                "child": "O3",
                "parent": "O2",
                "from_date": "2026-06-01",
                "until_date": None,
            }
        )
        # O2 -> O1 与 O3 -> O2 并存，另加 O2 -> O3 成环
        edges.append(
            {
                "edge_id": "responsibility-cycle",
                "perspective": "responsibility",
                "child": "O2",
                "parent": "O3",
                "from_date": "2026-06-01",
                "until_date": None,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=LEGAL_EDGES + edges,
                    assignments=[],
                    successions=[],
                    handovers=[],
                    services=[],
                )
            )
            report = warehouse.inspect()
            cycle_issues = [
                issue for issue in report["issues"] if issue["code"] == "cycle"
            ]
            self.assertTrue(cycle_issues)
            self.assertEqual(cycle_issues[0]["perspective"], "responsibility")
            legal = warehouse.validation.hierarchy_at("legal", date(2026, 7, 1))
            self.assertEqual(legal.status, "ok")
            with self.assertRaises(AllocationError):
                warehouse.validation.ancestors("responsibility", "O2", date(2026, 7, 1))
            before = warehouse.validation.hierarchy_at("responsibility", date(2026, 5, 1))
            self.assertEqual(before.status, "ok")


class DuplicateParent(unittest.TestCase):
    def test_two_parents_are_isolated(self) -> None:
        edges = list(RESP_EDGES) + [
            {
                "edge_id": "responsibility-extra",
                "perspective": "responsibility",
                "child": "O2",
                "parent": "O6",
                "from_date": "2026-03-01",
                "until_date": None,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=edges,
                    assignments=[],
                    successions=[],
                    handovers=[],
                    services=[],
                )
            )
            segment = warehouse.validation.hierarchy_at("responsibility", date(2026, 4, 1))
            self.assertEqual(segment.status, "duplicate_parent")
            with self.assertRaises(AllocationError):
                warehouse.validation.ancestors("responsibility", "O2", date(2026, 4, 1))


class IntervalsAndIdentity(unittest.TestCase):
    def test_interval_is_half_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=[],
                    assignments=[
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
                            "assignment_id": "A2",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O3",
                            "share": "1",
                            "from_date": "2026-11-01",
                            "until_date": None,
                        },
                    ],
                    successions=[],
                    handovers=[],
                    services=[],
                )
            )
            self.assertEqual(
                warehouse.validation.assignment_at(
                    "responsibility", "S1", "meal", date(2026, 10, 31)
                ).claims[0].org_id,
                "O2",
            )
            self.assertEqual(
                warehouse.validation.assignment_at(
                    "responsibility", "S1", "meal", date(2026, 11, 1)
                ).claims[0].org_id,
                "O3",
            )

    def test_rename_without_new_identity_keeps_org_id(self) -> None:
        renamed = [dict(org) for org in ORGS]
        renamed[1]["name"] = "合成机构2（改名）"
        renamed[1]["revision"] = 2
        with tempfile.TemporaryDirectory() as directory:
            original = Warehouse(
                make_dataset(
                    Path(directory) / "a",
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=[],
                    assignments=[],
                    successions=[],
                    handovers=[],
                    services=[],
                )
            )
            revised = Warehouse(
                make_dataset(
                    Path(directory) / "b",
                    organizations=renamed,
                    sites=SITES,
                    hierarchies=[],
                    assignments=[],
                    successions=[],
                    handovers=[],
                    services=[],
                )
            )
            self.assertEqual(
                original.validation.identity_digest(),
                revised.validation.identity_digest(),
            )
            self.assertNotEqual(
                original.validation.name_digest(), revised.validation.name_digest()
            )


class ThreePerspectives(unittest.TestCase):
    def test_legal_and_finance_do_not_follow_responsibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = _base(Path(directory))
            legal = warehouse.link("legal", "as_recorded", date(2026, 12, 1), date(2026, 12, 5))
            finance = warehouse.link("finance", "as_recorded", date(2026, 12, 1), date(2026, 12, 5))
            responsibility = warehouse.link(
                "responsibility", "as_recorded", date(2026, 12, 1), date(2026, 12, 5)
            )
            for result in (legal, finance, responsibility):
                self.assertEqual(len({row.service_id for row in result.allocations}), 8)
            self.assertTrue(all(row.source_org == "O2" for row in legal.allocations))
            f1_finance = [row for row in finance.allocations if row.service_id == "F1"]
            self.assertEqual(f1_finance[0].org_id, "O3")
            f1_resp = [row for row in responsibility.allocations if row.service_id == "F1"]
            self.assertEqual(f1_resp[0].org_id, "O2")


class ChainedSplitSuccession(unittest.TestCase):
    def test_multi_hop_split_keeps_share_total(self) -> None:
        """A→B 整体承接，B 再拆成 C/D 各 50%，合计仍为 1 且可追溯到每一步交接。"""
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=RESP_EDGES,
                    assignments=[
                        {
                            "assignment_id": "A1",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O2",
                            "share": "1",
                            "from_date": "2026-01-01",
                            "until_date": "2026-11-01",
                        }
                    ],
                    successions=[
                        {
                            "notice_id": "N1",
                            "from_org": "O2",
                            "to_org": "O6",
                            "sites": ["S1"],
                            "services": ["meal"],
                            "share": "1",
                            "effective_date": "2026-11-01",
                            "received_at": "2026-11-02T08:00:00+08:00",
                        },
                        {
                            "notice_id": "N2",
                            "from_org": "O6",
                            "to_org": "O3",
                            "sites": ["S1"],
                            "services": ["meal"],
                            "share": "0.5",
                            "effective_date": "2026-12-01",
                            "received_at": "2026-12-02T08:00:00+08:00",
                        },
                        {
                            "notice_id": "N3",
                            "from_org": "O6",
                            "to_org": "O7",
                            "sites": ["S1"],
                            "services": ["meal"],
                            "share": "0.5",
                            "effective_date": "2026-12-01",
                            "received_at": "2026-12-02T08:00:00+08:00",
                        },
                    ],
                    handovers=[
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
                            "from_date": "2026-12-01",
                            "until_date": None,
                        },
                        {
                            "handover_id": "H3",
                            "notice_id": "N3",
                            "site_id": "S1",
                            "service": "meal",
                            "from_date": "2026-12-01",
                            "until_date": None,
                        },
                    ],
                    services=[
                        {
                            "service_id": "F1",
                            "person_id": "P1",
                            "site_id": "S1",
                            "service": "meal",
                            "occurred_on": "2026-10-01",
                            "source_org": "O2",
                            "amount_fen": 1000,
                        }
                    ],
                )
            )
            result = warehouse.link(
                "responsibility", "restated", date(2026, 12, 31), date(2026, 12, 5)
            )
            rows = {row.org_id: row for row in result.allocations}
            self.assertEqual(set(rows), {"O3", "O7"})
            self.assertEqual(rows["O3"].share, Decimal("0.5"))
            self.assertEqual(rows["O7"].share, Decimal("0.5"))
            self.assertEqual(rows["O3"].notices, ("N1", "N2"))
            self.assertEqual(rows["O3"].handovers, ("H1", "H2"))
            self.assertEqual(sum(row.amount for row in result.allocations), 1000)
            self.assertTrue(warehouse.linker.check_conservation(result)["ok"])

    def test_cycle_in_succession_chain_stays_unmapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warehouse = Warehouse(
                make_dataset(
                    Path(directory),
                    organizations=ORGS,
                    sites=SITES,
                    hierarchies=RESP_EDGES,
                    assignments=[
                        {
                            "assignment_id": "A1",
                            "site_id": "S1",
                            "service": "meal",
                            "perspective": "responsibility",
                            "org_id": "O2",
                            "share": "1",
                            "from_date": "2026-01-01",
                            "until_date": "2026-11-01",
                        }
                    ],
                    successions=[
                        {
                            "notice_id": "N1",
                            "from_org": "O2",
                            "to_org": "O4",
                            "sites": ["S1"],
                            "services": ["meal"],
                            "share": "1",
                            "effective_date": "2026-11-01",
                            "received_at": "2026-11-02T08:00:00+08:00",
                        },
                        {
                            "notice_id": "N2",
                            "from_org": "O4",
                            "to_org": "O2",
                            "sites": ["S1"],
                            "services": ["meal"],
                            "share": "1",
                            "effective_date": "2026-11-15",
                            "received_at": "2026-11-16T08:00:00+08:00",
                        },
                    ],
                    handovers=[
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
                            "from_date": "2026-11-15",
                            "until_date": None,
                        },
                    ],
                    services=[
                        {
                            "service_id": "F1",
                            "person_id": "P1",
                            "site_id": "S1",
                            "service": "meal",
                            "occurred_on": "2026-10-01",
                            "source_org": "O2",
                            "amount_fen": 1000,
                        }
                    ],
                )
            )
            result = warehouse.link(
                "responsibility", "restated", date(2026, 12, 31), date(2026, 12, 5)
            )
            self.assertTrue(all(row.org_id is None for row in result.allocations))
            self.assertEqual(sum(row.share for row in result.allocations), Decimal(1))
            self.assertEqual(sum(row.amount for row in result.allocations), 1000)
            self.assertTrue(warehouse.linker.check_conservation(result)["ok"])


if __name__ == "__main__":
    unittest.main()
