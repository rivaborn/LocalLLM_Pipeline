"""Pytest conftest -- auto-log test results to test_archpipeline.log"""

import datetime
from pathlib import Path

LOG_PATH = Path(__file__).parent / "test_archpipeline.log"
_log_file = None


def pytest_configure(config):
    global _log_file
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(LOG_PATH, "w", encoding="utf-8")
    _log_file.write(f"Test run started at {datetime.datetime.now()}\n")
    _log_file.write("=" * 70 + "\n")


def pytest_runtest_logreport(report):
    if _log_file is None:
        return
    if report.when == "call" or (report.when == "setup" and report.failed):
        status = report.outcome.upper()
        line = f"{status:8s} {report.nodeid}"
        _log_file.write(line + "\n")
        if report.failed and report.longreprtext:
            _log_file.write(report.longreprtext + "\n")
        _log_file.flush()


def pytest_unconfigure(config):
    global _log_file
    if _log_file is not None:
        _log_file.write("=" * 70 + "\n")
        _log_file.write(f"Test run finished at {datetime.datetime.now()}\n")
        _log_file.close()
        _log_file = None
