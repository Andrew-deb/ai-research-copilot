"""The same attachment layout is used on every composer surface."""
import pytest
import upload_config


@pytest.mark.parametrize('url', ['/chat', '/chat?mode=wick', '/chat/assistant'])
def test_attachment_tray_precedes_prompt_and_tools_are_outside_form(client, monkeypatch, url):
    monkeypatch.setattr(upload_config, 'ENABLED', True)
    response = client.get(url)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    form_start = html.index('id="chat-composer"')
    form_end = html.index('</form>', form_start)
    assert form_start < html.index('id="upload-list"') < html.index('id="chat-input"') < form_end
    assert html.index('id="upload-details"') > form_end
    assert html.count('id="upload-list"') == 1
    assert html.count('id="upload-input"') == 1
