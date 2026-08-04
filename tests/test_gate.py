from __future__ import annotations

import os
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.gate import derive_bundled_gate, gate_exit_code  # noqa: E402


def finding(priority: int) -> dict[str, object]:
    return {
        "title": f"[P{priority}] Finding",
        "body": "Actionable defect",
        "confidence_score": 0.9,
        "priority": priority,
        "code_location": {
            "absolute_file_path": "/tmp/app.py",
            "line_range": {"start": 1, "end": 1},
        },
    }


class BundledGateTests(unittest.TestCase):
    def test_gate_exit_code_matrix(self) -> None:
        self.assertEqual(gate_exit_code("passed"), 0)
        self.assertEqual(gate_exit_code("passed_with_warnings"), 0)
        self.assertEqual(gate_exit_code("blocked"), 2)
        self.assertEqual(gate_exit_code("inconclusive"), 1)
        self.assertEqual(gate_exit_code("not_evaluated"), 1)
        self.assertEqual(gate_exit_code(None), 1)

    def test_clean_correct_review_passes(self) -> None:
        verdict, gate = derive_bundled_gate(
            {"findings": [], "overall_correctness": "patch is correct"}
        )

        self.assertEqual(verdict, "correct")
        self.assertEqual(gate, "passed")

    def test_p0_through_p2_block_but_p3_is_warning(self) -> None:
        for priority in range(3):
            with self.subTest(priority=priority):
                verdict, gate = derive_bundled_gate(
                    {
                        "findings": [finding(priority)],
                        "overall_correctness": "patch is incorrect",
                    }
                )
                self.assertEqual(verdict, "incorrect")
                self.assertEqual(gate, "blocked")

        verdict, gate = derive_bundled_gate(
            {
                "findings": [finding(3)],
                "overall_correctness": "patch is correct",
            }
        )
        self.assertEqual(verdict, "correct")
        self.assertEqual(gate, "passed_with_warnings")

    def test_p3_only_is_warning_even_when_overall_verdict_is_incorrect(self) -> None:
        verdict, gate = derive_bundled_gate(
            {
                "findings": [finding(3)],
                "overall_correctness": "patch is incorrect",
            }
        )

        self.assertEqual(verdict, "incorrect")
        self.assertEqual(gate, "passed_with_warnings")

    def test_incorrect_verdict_without_finding_is_inconclusive(self) -> None:
        verdict, gate = derive_bundled_gate(
            {"findings": [], "overall_correctness": "patch is incorrect"}
        )

        self.assertEqual(verdict, "incorrect")
        self.assertEqual(gate, "inconclusive")


if __name__ == "__main__":
    unittest.main()
