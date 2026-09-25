"""Shared test set-up: a temporary runtime folder with settings that point into it.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import tempfile
from pathlib import Path
from unittest import mock

from app import records


def temp_runtime(test):
    """Make a temporary folder, point DATA_ROOT and every path setting into it, and clean up after the test."""
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    root = Path(folder.name).resolve()
    runtime = root / "runtime-data"
    patcher = mock.patch.object(records, "DATA_ROOT", runtime)
    patcher.start()
    test.addCleanup(patcher.stop)
    settings = {
        "data_root": str(runtime),
        "deployment_file": str(runtime / "deployment.json"),
        "vaccination_file": str(runtime / "vaccination_record.json"),
        "vaccination_salt_file": str(runtime / "private" / "vaccination_salt.json"),
        "identity_directory": str(runtime / "identities"),
        "identity_salt_directory": str(runtime / "private"),
        "actor_account_indices": {"deployer": 0, "clinic": 1, "guardian": 2, "school": 3, "doctor": 4},
    }
    return root, settings
