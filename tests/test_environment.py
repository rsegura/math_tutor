"""Repository-level contract for the containerized development scaffold."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PYTHON_IMAGE = (
    "python:3.12.14-slim-bookworm"
    "@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)


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


def test_source_root_is_not_a_python_package() -> None:
    assert not (ROOT / "src/__init__.py").exists()


def test_dependency_sync_fails_closed_when_the_lockfile_drifts() -> None:
    for dockerfile_name in ("Dockerfile.agent", "Dockerfile.tooling"):
        dockerfile = (ROOT / "docker" / dockerfile_name).read_text()

        assert "UV_LOCKED=1" in dockerfile
        assert "uv sync --locked" in dockerfile
        assert "UV_FROZEN" not in dockerfile
        assert "--frozen" not in dockerfile


def test_python_base_image_is_pinned_to_a_patch_and_digest() -> None:
    for dockerfile_name in ("Dockerfile.agent", "Dockerfile.tooling"):
        dockerfile = (ROOT / "docker" / dockerfile_name).read_text()

        assert f"FROM {PYTHON_IMAGE}" in dockerfile


def test_live_model_target_preserves_failures_but_allows_no_tests() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert 'if [ "$$status" -eq 5 ]' in makefile
    assert 'exit "$$status"' in makefile
