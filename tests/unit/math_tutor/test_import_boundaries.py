"""Executable import rules for the canonical math-tutor package layers."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = ROOT / "src"

# Each layer may import itself and layers further inward. Infrastructure is the
# outermost adapter layer and may additionally import vendor SDKs.
ALLOWED_MATH_TUTOR_NAMESPACES = {
    "domain": ("math_tutor.domain",),
    "application": ("math_tutor.domain", "math_tutor.application"),
    "harness": (
        "math_tutor.domain",
        "math_tutor.application",
        "math_tutor.harness",
    ),
    "infrastructure": (
        "math_tutor.domain",
        "math_tutor.application",
        "math_tutor.harness",
        "math_tutor.infrastructure",
    ),
}

VENDOR_ROOTS = {"aiosqlite", "fastapi", "livekit", "openai", "sqlite3"}
VENDOR_FREE_LAYERS = {"domain", "application", "harness"}

# These unqualified packages belonged to the medical-screening repository.
# Math-tutor code must use its canonical ``math_tutor.*`` namespace instead of
# silently reaching into a sibling checkout or copied legacy module.
LEGACY_ROOTS = {
    "agent",
    "application",
    "domain",
    "infrastructure",
    "llm_harness",
    "protocols",
    "screening_agent",
}


@dataclass(frozen=True)
class ImportBoundaryViolation:
    path: Path
    line: int
    module: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: imports {self.module} ({self.reason})"


def _module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from_import(
    node: ast.ImportFrom, *, path: Path, source_root: Path
) -> str:
    if node.level == 0:
        return node.module or ""

    current = _module_name(path, source_root).split(".")
    if path.name != "__init__.py":
        current.pop()
    keep = len(current) - (node.level - 1)
    if keep <= 0:
        return "<relative-beyond-source-root>"
    resolved = current[:keep]
    if node.module:
        resolved.extend(node.module.split("."))
    return ".".join(resolved)


def _imports(path: Path, source_root: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_import(
                node, path=path, source_root=source_root
            )
            expands_aliases = (
                node.level == 0 and resolved == "math_tutor"
            ) or (
                node.level > 0
                and node.module is None
                and resolved != "<relative-beyond-source-root>"
            )
            if expands_aliases:
                for alias in node.names:
                    module = (resolved if alias.name == "*"
                              else f"{resolved}.{alias.name}")
                    yield node.lineno, module
            else:
                yield node.lineno, resolved


def _reason_for(module: str, layer: str) -> str | None:
    root = module.split(".", maxsplit=1)[0]
    if root == "src" or module == "<relative-beyond-source-root>":
        return "src is a source root, not an importable package"
    if root in LEGACY_ROOTS:
        return "legacy medical namespace"
    if layer in VENDOR_FREE_LAYERS and root in VENDOR_ROOTS:
        return "vendor dependency outside infrastructure"
    if root == "math_tutor":
        allowed = ALLOWED_MATH_TUTOR_NAMESPACES[layer]
        if not any(module == name or module.startswith(f"{name}.") for name in allowed):
            return f"outward dependency from {layer}"
        return None
    if layer in VENDOR_FREE_LAYERS and root not in sys.stdlib_module_names:
        return "third-party dependency outside infrastructure"
    return None


def _layer_violations(source_root: Path, layer: str):
    package_dir = source_root / "math_tutor" / layer
    modules = sorted(package_dir.rglob("*.py")) if package_dir.is_dir() else []
    for path in modules:
        for line, module in _imports(path, source_root):
            if reason := _reason_for(module, layer):
                yield ImportBoundaryViolation(
                    path=path.relative_to(source_root.parent),
                    line=line,
                    module=module,
                    reason=reason,
                )


def test_src_is_only_a_source_root() -> None:
    assert not (SOURCE_ROOT / "__init__.py").exists()


@pytest.mark.parametrize("layer", sorted(ALLOWED_MATH_TUTOR_NAMESPACES))
def test_each_architecture_layer_is_a_non_empty_package(layer: str) -> None:
    package_dir = SOURCE_ROOT / "math_tutor" / layer

    assert package_dir.is_dir(), f"missing package directory: {package_dir}"
    assert (package_dir / "__init__.py").is_file()
    assert list(package_dir.rglob("*.py")), f"no modules discovered under {package_dir}"


def test_real_math_tutor_tree_respects_import_direction() -> None:
    violations = [
        violation
        for layer in ALLOWED_MATH_TUTOR_NAMESPACES
        for violation in _layer_violations(SOURCE_ROOT, layer)
    ]

    assert violations == [], "\n".join(map(str, violations))


@pytest.mark.parametrize(
    ("layer", "source", "forbidden_module"),
    [
        ("domain", "from math_tutor.application import service", "math_tutor.application"),
        ("application", "from math_tutor.harness import loop", "math_tutor.harness"),
        ("harness", "from math_tutor.infrastructure import db", "math_tutor.infrastructure"),
        ("domain", "import livekit.agents", "livekit.agents"),
        ("domain", "from fastapi import FastAPI", "fastapi"),
        ("application", "import openai", "openai"),
        ("application", "import sqlite3", "sqlite3"),
        ("application", "import requests", "requests"),
        ("harness", "import aiosqlite", "aiosqlite"),
        (
            "application",
            "from ...json import loads",
            "<relative-beyond-source-root>",
        ),
        ("domain", "import src", "src"),
        ("domain", "from src.math_tutor.domain import model", "src.math_tutor.domain"),
        ("domain", "from domain import screening", "domain"),
        ("application", "from application import screening_service", "application"),
        ("harness", "from llm_harness import runtime", "llm_harness"),
        ("infrastructure", "from infrastructure import persistence", "infrastructure"),
        ("infrastructure", "from agent import screening_agent", "agent"),
    ],
)
def test_boundary_scan_rejects_forbidden_imports_in_temporary_tree(
    tmp_path: Path, layer: str, source: str, forbidden_module: str
) -> None:
    source_root = tmp_path / "src"
    package = source_root / "math_tutor" / layer
    package.mkdir(parents=True)
    (package / "probe.py").write_text(f"{source}\n", encoding="utf-8")

    violations = list(_layer_violations(source_root, layer))

    assert [violation.module for violation in violations] == [forbidden_module]


@pytest.mark.parametrize(
    ("layer", "source"),
    [
        ("domain", "from dataclasses import dataclass\nfrom . import values"),
        ("domain", "from math_tutor import domain"),
        (
            "application",
            "from math_tutor.domain import values\n"
            "from .ports import StorePort\n"
            "from .. import domain",
        ),
        (
            "harness",
            "from math_tutor.application.ports import ModelPort\n"
            "from . import validation\n"
            "from .. import application",
        ),
        (
            "infrastructure",
            "import fastapi\nfrom math_tutor.application import services",
        ),
    ],
)
def test_boundary_scan_accepts_allowed_imports_in_temporary_tree(
    tmp_path: Path, layer: str, source: str
) -> None:
    source_root = tmp_path / "src"
    package = source_root / "math_tutor" / layer
    package.mkdir(parents=True)
    (package / "probe.py").write_text(f"{source}\n", encoding="utf-8")

    assert list(_layer_violations(source_root, layer)) == []
