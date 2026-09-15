"""Static boundary tests for the pure domain layer."""

import ast
from pathlib import Path

ALLOWED_IMPORT_ROOTS = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "direction_engine_v3",
    "enum",
    "typing",
}
FORBIDDEN_CALL_NAMES = {
    "connect",
    "create_connection",
    "execute",
    "place_order",
    "request",
    "submit",
}


def test_domain_has_no_network_database_or_sdk_dependencies() -> None:
    domain_root = Path(__file__).resolve().parents[2] / "src" / "direction_engine_v3" / "domain"
    violations: list[str] = []

    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", maxsplit=1)[0] for alias in node.names}
                violations.extend(
                    f"{path.name}: import {root}"
                    for root in roots
                    if root not in ALLOWED_IMPORT_ROOTS
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", maxsplit=1)[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                    violations.append(f"{path.name}: call {node.func.id}")
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in FORBIDDEN_CALL_NAMES
                ):
                    violations.append(f"{path.name}: call .{node.func.attr}")

    assert violations == []
