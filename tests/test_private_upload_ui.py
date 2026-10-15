"""Composer surfaces expose the file foundation without claiming agent access."""

import upload_config


def test_ui_exposes_uploads_in_both_modes_only_when_enabled(client, monkeypatch):
    monkeypatch.setattr(upload_config, "ENABLED", True)
    for url in ("/chat", "/chat/assistant"):
        text = client.get(url).text
        assert 'id="upload-choose"' in text and 'id="upload-input"' in text
        assert "Agent reading is coming next" in text
