"""Mode isolation: credentials stay server-side, tools/cache/prompt stay mode-bound."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import config
from exceptions import CapabilityDeniedError, ExternalAPIError
from services import agent_service, mcp_client


def test_endpoint_configuration_never_falls_back_for_wick(monkeypatch):
    monkeypatch.setattr(config, "RESEARCH_MCP_SERVER_URL", "https://research/mcp")
    monkeypatch.setattr(config, "WICK_MCP_SERVER_URL", None)
    for name in ("DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
        monkeypatch.setattr(config, name, "configured")
    assert config.mcp_is_configured("research")
    assert not config.mcp_is_configured("wick")
    assert config.mcp_endpoint("wick") is None
    with pytest.raises(ValueError):
        config.mcp_endpoint("forged")
    with pytest.raises(ExternalAPIError):
        mcp_client.list_tools(mode="wick")


def test_mode_tier_and_write_gate_are_independent(monkeypatch):
    assert len(agent_service.callable_tools("authenticated", "wick")) == 16
    assert len(agent_service.callable_tools("authenticated", "research")) == 13
    assert set(agent_service.callable_tools("anonymous", "wick")) == {
        "find_workspace_resources", "get_workspace_resource", "get_workspace_paper", "search_workspace_papers"}
    for tool, mode in (("search_papers", "wick"), ("create_note", "research"),
                       ("generate_reading_plan", "wick")):
        with pytest.raises(CapabilityDeniedError):
            agent_service.ensure_callable("authenticated", tool, mode)
    monkeypatch.setattr(agent_service, "WRITE_TOOLS_ENABLED", False)
    assert "get_reading_progress" in agent_service.callable_tools("authenticated", "wick")
    assert "create_note" not in agent_service.callable_tools("authenticated", "wick")


def test_wick_has_independent_prompt_and_preserves_resource_results():
    prompt = agent_service.build_system_prompt("authenticated", "wick")
    assert "Your workspace assistant" in prompt
    assert "# Wick" in prompt
    assert agent_service._base_prompt() not in prompt
    note = {"note_id": "note", "title": "Heading", "tags": ["test"], "note_text": "Body"}
    assert agent_service._evidence(note, "wick") == note


def test_missing_wick_prompt_uses_workspace_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_service, "_wick_prompt_cache", None)
    monkeypatch.setattr(agent_service, "_WICK_PROMPT_PATH", tmp_path / "missing.md")
    assert agent_service._wick_prompt() == agent_service._WICK_FALLBACK_PROMPT


def test_tool_schemas_cached_by_mode_and_endpoint(monkeypatch):
    monkeypatch.setattr(config, "RESEARCH_MCP_SERVER_URL", "https://research/mcp")
    monkeypatch.setattr(config, "WICK_MCP_SERVER_URL", "https://wick/mcp")
    calls = []

    @asynccontextmanager
    async def session(user_id, mode="research"):
        calls.append((mode, config.mcp_endpoint(mode)))
        class FakeSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(
                    name=mode, description="", inputSchema={})])
        yield FakeSession()

    monkeypatch.setattr(mcp_client, "_session", session)
    mcp_client.reset_cache()
    try:
        assert mcp_client.list_tools()[0]["name"] == "research"
        assert mcp_client.list_tools(mode="wick")[0]["name"] == "wick"
        mcp_client.list_tools(mode="wick")
        assert len(calls) == 2
        monkeypatch.setattr(config, "WICK_MCP_SERVER_URL", "https://new-wick/mcp")
        mcp_client.list_tools(mode="wick")
        assert calls[-1] == ("wick", "https://new-wick/mcp")
        mcp_client.list_tools(force=True)
        assert calls[-1][0] == "research"
    finally:
        mcp_client.reset_cache()


def test_turn_binds_selected_session_and_identity(monkeypatch):
    calls = []
    @asynccontextmanager
    async def session(user_id, mode="research"):
        calls.append((user_id, mode))
        yield object()
    monkeypatch.setattr(mcp_client, "_session", session)
    assert mcp_client.Turn("owner", mode="wick").run(lambda call: "ok") == "ok"
    assert calls == [("owner", "wick")]


def test_unconfigured_wick_does_not_charge_or_call_research(client, monkeypatch):
    from routes import chat
    monkeypatch.setattr(mcp_client, "is_configured", lambda mode="research": mode == "research")
    monkeypatch.setattr(agent_service.llm_client,
                        "is_available", lambda: True)
    monkeypatch.setattr(chat, "consume_quota", lambda metric: pytest.fail("Unavailable Wick consumed quota"))
    monkeypatch.setattr(mcp_client, "list_tools", lambda *a, **k: pytest.fail("Called MCP"))
    response = client.post("/chat/ask", json={"question": "Show my notes", "chat_mode": "wick"})
    assert response.status_code == 503


def test_large_workspace_result_is_explicitly_truncated_valid_json():
    result = {"items": [{"title": "x" * 200, "id": str(i)} for i in range(200)]}
    content = agent_service._tool_content(result, "wick")
    assert len(content) < agent_service.MAX_TOOL_RESULT_CHARS
    assert json.loads(content)["truncated"] is True


def test_wick_refuses_wrong_server_catalog(monkeypatch):
    monkeypatch.setattr(mcp_client, "list_tools", lambda **kwargs: [
        {"name": "search_papers", "description": "", "input_schema": {}}])
    with pytest.raises(ExternalAPIError, match="incompatible"):
        agent_service._tool_schemas("authenticated", "wick")


def test_wick_refreshes_stale_catalog_once(monkeypatch):
    calls = []
    def listed(**kwargs):
        calls.append(kwargs)
        names = agent_service.WICK_TOOLS if kwargs.get("force") else agent_service.WICK_TOOLS - {"search_workspace_papers"}
        return [{"name": name, "description": "", "input_schema": {}} for name in names]
    monkeypatch.setattr(mcp_client, "list_tools", listed)
    result = agent_service._tool_schemas("authenticated", "wick")
    assert {item["function"]["name"] for item in result} == agent_service.WICK_TOOLS
    assert calls == [{"mode": "wick"}, {"force": True, "mode": "wick"}]


def test_wick_persistent_mismatch_logs_names_and_stops(monkeypatch, caplog):
    calls = []
    def listed(**kwargs):
        calls.append(kwargs)
        return [{"name": "search_papers", "description": "", "input_schema": {}}]
    monkeypatch.setattr(mcp_client, "list_tools", listed)
    with pytest.raises(ExternalAPIError, match="incompatible"):
        agent_service._tool_schemas("authenticated", "wick")
    assert len(calls) == 2
    assert "missing=" in caplog.text and "search_workspace_papers" in caplog.text
    assert "unexpected=['search_papers']" in caplog.text


def test_wick_valid_catalog_does_not_refresh(monkeypatch):
    calls = []
    def listed(**kwargs):
        calls.append(kwargs)
        return [{"name": name, "description": "", "input_schema": {}} for name in agent_service.WICK_TOOLS]
    monkeypatch.setattr(mcp_client, "list_tools", listed)
    agent_service._tool_schemas("authenticated", "wick")
    assert calls == [{"mode": "wick"}]
