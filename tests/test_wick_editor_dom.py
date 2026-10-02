"""Optional DOM runtime coverage; jsdom is a test dependency, not an app dependency."""
import subprocess
from pathlib import Path

import pytest


def test_inline_editor_and_typed_context_shortcuts():
    if subprocess.run(['node', '-e', "require.resolve('jsdom')"], capture_output=True).returncode:
        pytest.skip('Optional jsdom test runtime is unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('wick_editor_dom.cjs'))],
                   check=True, capture_output=True, text=True)


def test_action_approval_recovery_and_decision():
    if subprocess.run(['node', '-e', "require.resolve('jsdom')"], capture_output=True).returncode:
        pytest.skip('Optional jsdom test runtime is unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('action_approval_dom.cjs'))],
                   check=True, capture_output=True, text=True)
