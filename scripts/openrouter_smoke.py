"""Run the single OpenRouter smoke and classify its structured pytest result."""

from __future__ import annotations

import sys

import pytest


_NO_RUN_OPTIONS = frozenset(
    {
        "--collect-only",
        "--co",
        "--setup-only",
        "--setup-plan",
        "--fixtures",
        "--fixtures-per-test",
        "--help",
        "--version",
    }
)


class OutcomeReporter:
    def __init__(self) -> None:
        self.passed = 0
        self.skipped = 0
        self.failed = 0

    def pytest_runtest_logreport(self, report) -> None:
        if report.when != "call":
            return
        if report.passed:
            self.passed += 1
        elif report.skipped:
            self.skipped += 1
        elif report.failed:
            self.failed += 1


def main(arguments: list[str]) -> int:
    if any(argument.split("=", 1)[0] in _NO_RUN_OPTIONS for argument in arguments):
        print("FAIL: OpenRouter Responses tool smoke (no-run pytest option rejected)")
        return 2

    reporter = OutcomeReporter()
    status = pytest.main(
        [
            "-m",
            "live_llm",
            "-rA",
            "tests/live_llm/test_openrouter_responses_live.py",
            *arguments,
        ],
        plugins=[reporter],
    )
    if status == pytest.ExitCode.OK and reporter.passed == 1 and reporter.skipped == 0:
        print("PASS: OpenRouter Responses tool smoke")
        return 0
    if status == pytest.ExitCode.OK and reporter.skipped == 1 and reporter.passed == 0:
        print("SKIP: OpenRouter credentials or model not configured")
        return 0

    print("FAIL: OpenRouter Responses tool smoke")
    return int(status) if status != pytest.ExitCode.OK else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
