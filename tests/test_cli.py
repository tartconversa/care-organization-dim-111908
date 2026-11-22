"""命令行端到端：检查、关联、发布、历史、撤回、对照。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "careorg", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"命令失败({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return json.loads(completed.stdout)


class CommandLine(unittest.TestCase):
    def test_full_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / "store")
            report = run("inspect")
            self.assertEqual(report["error_count"], 0)
            self.assertEqual(report["source_rows"]["services"], 24)

            linked = run(
                "link",
                "--perspective",
                "responsibility",
                "--mode",
                "restated",
                "--observation-date",
                "2026-12-01",
                "--as-of",
                "2026-12-05",
            )
            self.assertEqual(linked["observation_date"], "2026-12-01")
            self.assertGreater(linked["allocation_count"], 24)

            summary = run(
                "details",
                "--perspective",
                "responsibility",
                "--mode",
                "restated",
                "--observation-date",
                "2026-12-01",
                "--as-of",
                "2026-12-05",
                "--level",
                "unit",
            )
            totals = summary["totals"]
            self.assertEqual(totals["share"], "24")
            self.assertEqual(totals["service_count"], "24")
            self.assertEqual(totals["amount_fen"], 21900)

            recorded = run(
                "publish",
                "--store",
                store,
                "--key",
                "m1",
                "--label",
                "研究月度",
                "--perspective",
                "responsibility",
                "--mode",
                "as_recorded",
                "--observation-date",
                "2026-10-31",
                "--as-of",
                "2026-11-20T00:00:00+08:00",
            )
            restated = run(
                "publish",
                "--store",
                store,
                "--key",
                "op1",
                "--label",
                "运营当前",
                "--perspective",
                "responsibility",
                "--mode",
                "restated",
                "--observation-date",
                "2026-12-01",
                "--as-of",
                "2026-12-05T00:00:00+08:00",
            )
            self.assertEqual(restated["status"], "active")

            history = run("history", "--store", store)
            self.assertEqual({item["key"] for item in history}, {"m1", "op1"})

            comparison = run(
                "compare", "--store", store, "--left", "m1", "--right", "op1"
            )
            self.assertEqual(comparison["amount_mismatches"], 0)

            withdrawn = run(
                "withdraw",
                "--store",
                store,
                "--withdrawal-id",
                "W1",
                "--notice",
                "N1",
                "--effective-at",
                "2026-12-10T00:00:00+08:00",
                "--reason",
                "通知撤回",
            )
            self.assertIn("op1", {item["key"] for item in withdrawn["affected"]})

    def test_negative_source_is_reported(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "careorg", "inspect", "--root", "/nonexistent-path"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["code"], "catalog_missing")

    def test_invalid_arguments_report_json_error(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "careorg",
                "publish",
                "--key",
                "x",
                "--label",
                "x",
                "--perspective",
                "finance",
                "--mode",
                "restated",
                "--as-of",
                "2026-12-05T00:00:00+08:00",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["code"], "restated_not_supported")


if __name__ == "__main__":
    unittest.main()
