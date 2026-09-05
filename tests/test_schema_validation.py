from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ENTRYPOINT, init_git_fixture, make_fake_codex, read_fake_log, run_cli

sys.path.insert(0, str(ENTRYPOINT.parent))
from codex_reviewer.schema import SchemaValidationError, load_validator, result_error


class CustomSchemaTests(unittest.TestCase):
    def test_valid_and_invalid_results_use_the_same_schema(self):
        schema = {
            "type": "object", "required": ["verdict"], "additionalProperties": False,
            "properties": {"verdict": {"$ref": "#/$defs/verdict"}},
            "$defs": {"verdict": {"type": "object", "required": ["ok"],
                                   "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            binary = make_fake_codex(root / "fake")
            path = root / "schema.json"
            path.write_text(json.dumps(schema))
            validator = load_validator(path)
            good = {"verdict": {"ok": True}}
            self.assertIsNone(result_error(validator, good))
            for payload in ([], {}, {"verdict": "SECRET-VALUE"}, {"verdict": {}},
                            {"verdict": {"ok": "SECRET-VALUE"}}, {"verdict": {"ok": True}, "extra": 1}):
                with self.subTest(payload=payload):
                    result_path = root / "result.json"
                    result = run_cli(
                        "custom", "review", "--cd", str(repo), "--codex-bin", str(binary),
                        "--schema", str(path), "--result-json", str(result_path),
                        env={"FAKE_CODEX_FINAL": json.dumps(payload)},
                    )
                    self.assertEqual(result.returncode, 1)
                    envelope = json.loads(result_path.read_text())
                    self.assertFalse(envelope["success"])
                    self.assertIn("schema validation failed at /", envelope["error"])
                    self.assertNotIn("SECRET-VALUE", result.stderr)
            result = run_cli(
                "custom", "review", "--cd", str(repo), "--codex-bin", str(binary),
                "--schema", str(path), "--result-json", str(root / "result.json"),
                env={"FAKE_CODEX_FINAL": json.dumps(good)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / "result.json").read_text())["gate_status"], "not_evaluated")

    def test_bad_schemas_fail_before_inference(self):
        schemas = [
            {"type": "object", "required": "invalid"},
            {"type": "object", "$schema": "https://example.invalid/unknown"},
            {"type": "object", "$ref": "https://example.invalid/schema"},
            {"type": "object", "$ref": "file:///tmp/schema.json"},
            {"type": "object", "$ref": "#/missing"},
            {"type": "object", "$defs": {"unused": {"$ref": "https://example.invalid/schema"}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root / "fake")
            path = root / "schema.json"
            for index, schema in enumerate(schemas):
                with self.subTest(schema=schema):
                    log = root / f"calls-{index}.json"
                    path.write_text(json.dumps(schema))
                    result = run_cli(
                        "custom", "review", "--codex-bin", str(binary), "--schema", str(path),
                        env={"FAKE_CODEX_LOG": str(log)},
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(any("exec" in call["argv"] for call in read_fake_log(log)))

    def test_missing_dependency_fails_without_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root / "fake")
            path = root / "schema.json"
            path.write_text('{"type":"object"}')
            log = root / "calls.json"
            result = subprocess.run(
                [sys.executable, "-S", str(ENTRYPOINT), "custom", "review", "--codex-bin",
                 str(binary), "--schema", str(path)],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "FAKE_CODEX_LOG": str(log)},
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("requirements-schema.txt", result.stderr)
            self.assertFalse(any("exec" in call["argv"] for call in read_fake_log(log)))

    def test_referenced_extension_rules_fail_before_inference(self):
        targets = [
            {"$ref": "https://example.invalid/external.json"},
            {"$ref": "file:///tmp/external.json"},
            {"type": "object", "properties": {"nested": {"$ref": "https://example.invalid/schema"}}},
            {"type": "invalid"},
            {"$schema": "https://example.invalid/dialect"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root / "fake")
            path = root / "schema.json"
            for index, target in enumerate(targets):
                with self.subTest(target=target):
                    schema = {"type": "object", "properties": {"value": {"$ref": "#/components/alias"}},
                              "components": {"alias": {"$ref": "#/components/value"}, "value": target}}
                    path.write_text(json.dumps(schema))
                    log = root / f"calls-{index}.json"
                    result = run_cli(
                        "custom", "review", "--codex-bin", str(binary), "--schema", str(path),
                        env={"FAKE_CODEX_LOG": str(log)},
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(any("exec" in call["argv"] for call in read_fake_log(log)))

    def test_local_recursive_extension_rules_remain_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            for dialect in ("http://json-schema.org/draft-07/schema#", "https://json-schema.org/draft/2020-12/schema"):
                with self.subTest(dialect=dialect):
                    schema = {"$schema": dialect, "type": "object", "$ref": "#/components/node",
                              "components": {"node": {"type": "object", "properties": {
                                  "value": {"type": "integer"}, "next": {"$ref": "#/components/node"}}}}}
                    path.write_text(json.dumps(schema))
                    validator = load_validator(path)
                    self.assertIsNone(result_error(validator, {"value": 1, "next": {"value": 2}}))
                    self.assertIn("/next/value", result_error(validator, {"next": {"value": "invalid"}}))

    def test_known_dialects_anchors_and_format_annotations(self):
        for dialect in (None, "http://json-schema.org/draft-07/schema#", "https://json-schema.org/draft/2020-12/schema"):
            with self.subTest(dialect=dialect), tempfile.TemporaryDirectory() as tmp:
                schema = {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}
                if dialect:
                    schema["$schema"] = dialect
                path = Path(tmp) / "schema.json"
                path.write_text(json.dumps(schema))
                self.assertIsNone(result_error(load_validator(path), {"email": "not an email"}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            path.write_text(json.dumps({"type": "object", "$defs": {"value": {"$anchor": "value", "type": "string"}},
                                        "properties": {"value": {"$ref": "#value"}, "const": {"const": {"$ref": "literal data"}}}}))
            self.assertIsNone(result_error(load_validator(path), {"value": "ok", "const": {"$ref": "literal data"}}))


if __name__ == "__main__":
    unittest.main()
