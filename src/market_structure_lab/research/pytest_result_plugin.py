"""Small pytest plugin that emits exact per-node status evidence for one shard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_STATUSES: dict[str, str] = {}


def pytest_sessionstart(session: Any) -> None:
    """Reset process-local status state before a shard run."""

    del session
    _STATUSES.clear()


def pytest_runtest_logreport(report: Any) -> None:
    """Record the terminal status for each executed pytest node id."""

    nodeid = str(report.nodeid)
    if report.when == "setup":
        if report.skipped:
            _STATUSES[nodeid] = "skipped"
        elif report.failed:
            _STATUSES[nodeid] = "failed"
    elif report.when == "call":
        if report.passed:
            _STATUSES[nodeid] = "passed"
        elif report.skipped:
            _STATUSES[nodeid] = "skipped"
        else:
            _STATUSES[nodeid] = "failed"
    elif report.when == "teardown" and report.failed:
        _STATUSES[nodeid] = "failed"


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Write bounded JSON status rows for the parent verification runner."""

    del session
    output = os.environ.get("MSL_PYTEST_RESULT_PATH")
    if output is None:
        return
    payload = {
        "exit_status": int(exitstatus),
        "statuses": {nodeid: _STATUSES[nodeid] for nodeid in sorted(_STATUSES)},
    }
    Path(output).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
