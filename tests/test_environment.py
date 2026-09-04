"""Repository-level contract for the containerized development scaffold."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_runs_on_python_312() -> None:
    assert sys.version_info[:2] == (3, 12)


def test_critical_runtime_dependencies_are_importable() -> None:
    for module_name in ("fastapi", "livekit.agents", "openai", "yaml"):
        importlib.import_module(module_name)


def test_required_scaffold_files_exist() -> None:
    required_paths = (
        "AGENTS.md",
        "pyproject.toml",
        "uv.lock",
        "docker-compose.yml",
        "docker/Dockerfile.agent",
        "docker/Dockerfile.tooling",
        "docker/livekit.yaml",
        "src/math_tutor/__init__.py",
        "web/__init__.py",
        "web/static/.gitkeep",
    )

    assert all((ROOT / path).exists() for path in required_paths)


def test_project_metadata_names_only_the_math_tutor_product() -> None:
    with (ROOT / "pyproject.toml").open("rb") as config_file:
        project = tomllib.load(config_file)["project"]

    metadata = f"{project['name']} {project['description']}".lower()
    legacy_terms = ("screen" + "ing", "mig" + "raine", "clinical" + " trial")

    assert project["name"] == "math-tutor-voice-poc"
    assert all(term not in metadata for term in legacy_terms)


def test_compose_defines_the_minimum_local_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())

    assert set(compose["services"]) == {"livekit", "tooling", "agent", "web"}
