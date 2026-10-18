"""Chat chrome stays scoped to the composer surfaces."""
import pytest


@pytest.mark.parametrize('url', ['/chat', '/chat?mode=wick', '/chat/assistant'])
def test_chat_allowance_precedes_composer_and_footer_is_absent(client, url):
    response = client.get(url)
    assert response.status_code == 200
    text = response.text
    assert 'class="app-footer"' not in text
    assert text.index('class="chat-note"') < text.index('id="chat-composer"')
    assert 'js/chat_viewport.js' in text


def test_non_chat_page_retains_footer(client):
    assert 'class="app-footer"' in client.get('/collections').text


def test_public_landing_uses_chat_layout(anon_client):
    text = anon_client.get('/').text
    assert 'class="app-footer"' not in text
    assert 'js/chat_viewport.js' in text
