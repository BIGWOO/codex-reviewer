"""Optional, offline validation of caller-supplied JSON Schemas and results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


class SchemaValidationError(ValueError):
    pass


def _pointer(parts) -> str:
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _subschemas(schema):
    """Visit schema positions, never data stored in const/default/enum/examples."""
    if not isinstance(schema, Mapping):
        return
    yield schema
    for key in (
        "properties", "patternProperties", "$defs", "definitions",
        "dependentSchemas", "dependencies",
    ):
        children = schema.get(key)
        if isinstance(children, Mapping):
            for child in children.values():
                yield from _subschemas(child)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = schema.get(key)
        if isinstance(children, list):
            for child in children:
                yield from _subschemas(child)
    for key in (
        "items", "additionalItems", "contains", "additionalProperties",
        "unevaluatedProperties", "unevaluatedItems", "propertyNames",
        "not", "if", "then", "else", "contentSchema",
    ):
        child = schema.get(key)
        for item in (child if isinstance(child, list) else [child]):
            yield from _subschemas(item)


def load_validator(path: Path):
    # Lazy imports keep the default reviewer free of optional dependencies.
    try:
        from jsonschema import Draft202012Validator, validators
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012, specification_with
    except ImportError as exc:
        raise SchemaValidationError(
            "Custom --schema requires jsonschema; install requirements-schema.txt in your Python environment"
        ) from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SchemaValidationError("Could not read custom JSON Schema") from exc
    if not isinstance(payload, dict) or payload.get("type") != "object":
        raise SchemaValidationError("Custom JSON Schema must describe an object")
    try:
        cls = (
            validators.validator_for(payload, default=None)
            if "$schema" in payload else Draft202012Validator
        )
        if cls is None:
            raise SchemaValidationError("Unsupported JSON Schema dialect")
        cls.check_schema(payload)
        def check_rules(contents):
            for schema in _subschemas(contents):
                if "$schema" in schema and validators.validator_for(schema, default=None) is None:
                    raise SchemaValidationError("Unsupported JSON Schema dialect")
                for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    if key in schema and (
                        not isinstance(schema[key], str) or not schema[key].startswith("#")
                    ):
                        raise SchemaValidationError(
                            "Custom JSON Schema references must stay inside the document"
                        )

        check_rules(payload)
        resource = Resource.from_contents(payload, default_specification=DRAFT202012)
        uri = resource.id() or "urn:codex-reviewer:schema"
        registry = Registry().with_resource(uri, resource)

        # Use each subresource's resolver so local IDs and anchors retain scope.
        visited = set()

        def check_references(current, resolver, inherited_cls=cls, *, enter=True):
            contents = current.contents
            current_cls = validators.validator_for(contents, default=inherited_cls)
            key = (id(contents), current_cls)
            if key in visited:
                return
            visited.add(key)
            current_cls.check_schema(contents)
            check_rules(contents)
            if enter:
                resolver = resolver.in_subresource(current)
            if isinstance(contents, Mapping):
                for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    if key in contents:
                        resolved = resolver.lookup(contents[key])
                        dialect = current_cls.META_SCHEMA.get("$id") or current_cls.META_SCHEMA["id"]
                        target = Resource.from_contents(
                            resolved.contents, default_specification=specification_with(dialect)
                        )
                        # lookup already supplies the target's resolver scope.
                        check_references(target, resolved.resolver, current_cls, enter=False)
            for child in current.subresources():
                check_references(child, resolver, current_cls)

        check_references(resource, registry.resolver(uri))
        return cls(payload, registry=registry)
    except SchemaValidationError:
        raise
    except Exception as exc:
        # Library exception messages may contain schema or instance contents.
        location = _pointer(getattr(exc, "absolute_schema_path", ()))
        raise SchemaValidationError(f"Invalid custom JSON Schema at {location}") from exc


def result_error(validator, payload: object):
    try:
        error = next(validator.iter_errors(payload), None)
        if error is not None:
            return f"Custom schema validation failed at {_pointer(error.absolute_path)} (rule: {error.validator})"
    except Exception:
        return "Custom schema validation could not resolve the supplied rules"
    return None
