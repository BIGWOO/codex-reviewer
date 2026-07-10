from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "references" / "review_output_schema.json"


class StructuredReviewSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_top_level_matches_native_review_shape(self) -> None:
        properties = self.schema["properties"]
        self.assertEqual(
            set(properties),
            {
                "findings",
                "overall_correctness",
                "overall_explanation",
                "overall_confidence_score",
            },
        )
        self.assertEqual(set(self.schema["required"]), set(properties))
        self.assertFalse(self.schema["additionalProperties"])

    def test_findings_use_p0_to_p3_and_precise_location(self) -> None:
        finding = self.schema["properties"]["findings"]["items"]
        fields = finding["properties"]
        self.assertEqual(fields["priority"]["minimum"], 0)
        self.assertEqual(fields["priority"]["maximum"], 3)
        self.assertEqual(fields["confidence_score"]["minimum"], 0)
        self.assertEqual(fields["confidence_score"]["maximum"], 1)
        location = fields["code_location"]
        self.assertIn("line_range", location["properties"])
        line_range = location["properties"]["line_range"]
        self.assertEqual(set(line_range["required"]), {"start", "end"})

    def test_runtime_metadata_is_not_embedded_in_review_schema(self) -> None:
        serialized = json.dumps(self.schema)
        for key in ("model", "effort", "usage", "timeout", "command", "binary"):
            self.assertNotIn(f'"{key}"', serialized)


if __name__ == "__main__":
    unittest.main()
