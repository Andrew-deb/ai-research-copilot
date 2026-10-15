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


def test_streamed_approval_keeps_panel_until_task_finishes():
    if subprocess.run(['node', '-e', "require.resolve('jsdom')"], capture_output=True).returncode:
        pytest.skip('Optional jsdom test runtime is unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('action_approval_stream_dom.cjs'))],
                   check=True, capture_output=True, text=True)


def test_external_picker_selected_import_and_membership_retry():
    if subprocess.run(['node', '-e', "require.resolve('jsdom')"], capture_output=True).returncode:
        pytest.skip('Optional jsdom test runtime is unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('external_paper_picker_dom.cjs'))],
                   check=True, capture_output=True, text=True)


def test_private_upload_controls_preserve_prompt_and_bind_conversation():
    if subprocess.run(['node', '-e', "require.resolve('jsdom')"], capture_output=True).returncode:
        pytest.skip('Optional jsdom test runtime is unavailable')
    subprocess.run(['node', str(Path(__file__).with_name('private_uploads_dom.cjs'))],
                   check=True, capture_output=True, text=True)
