"""Tests for MCP provider detection, especially web search precedence."""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from agent_team.mcp.providers import detect_provider, get_provider_by_name
from agent_team.mcp.providers.websearch import WebSearchProvider
from agent_team.mcp.providers.api import APIProvider


@dataclass
class MockTool:
    """Minimal tool mock matching MCPTool interface."""
    name: str
    input_schema: dict


class TestWebSearchDetection:
    """Test that Tavily tools are detected as 'websearch' provider."""

    def test_tavily_search_detected(self):
        tools = [MockTool(name="tavily-search", input_schema={"properties": {"query": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "websearch"

    def test_tavily_extract_detected(self):
        tools = [MockTool(name="tavily-extract", input_schema={"properties": {"urls": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "websearch"

    def test_tavily_crawl_detected(self):
        tools = [MockTool(name="tavily-crawl", input_schema={"properties": {"url": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "websearch"

    def test_generic_web_search_detected(self):
        tools = [MockTool(name="web_search", input_schema={"properties": {"q": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "websearch"


class TestWebSearchPrecedence:
    """Critical: web search must take precedence over API provider for overlapping params."""

    def test_tavily_extract_not_detected_as_api(self):
        """tavily-extract has 'urls' param which overlaps with APIProvider's 'url' detect_params.
        It MUST be detected as websearch, not api."""
        tools = [MockTool(name="tavily-extract", input_schema={"properties": {"urls": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "websearch", f"Expected 'websearch' but got '{provider.name}'"
        assert not isinstance(provider, APIProvider)

    def test_tavily_crawl_with_url_param_not_api(self):
        """tavily-crawl has a 'url' param — exact overlap with APIProvider."""
        tools = [MockTool(name="tavily-crawl", input_schema={"properties": {"url": {}}})]
        provider = detect_provider(tools)
        assert provider.name == "websearch"


class TestExistingProviderBackcompat:
    """Ensure existing providers still work after adding tool-name matching."""

    def test_database_still_detected(self):
        tools = [MockTool(name="execute_sql", input_schema={"properties": {"sql": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "database"

    def test_filesystem_still_detected(self):
        tools = [MockTool(name="read_file", input_schema={"properties": {"path": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "filesystem"

    def test_api_still_detected_for_non_tavily(self):
        tools = [MockTool(name="call_api", input_schema={"properties": {"url": {}, "method": {}}})]
        provider = detect_provider(tools)
        assert provider is not None
        assert provider.name == "api"

    def test_no_provider_for_unknown(self):
        tools = [MockTool(name="unknown_tool", input_schema={"properties": {"foo": {}}})]
        provider = detect_provider(tools)
        assert provider is None


class TestProviderLookup:
    def test_get_websearch_by_name(self):
        provider = get_provider_by_name("websearch")
        assert provider is not None
        assert isinstance(provider, WebSearchProvider)

    def test_get_nonexistent_returns_none(self):
        assert get_provider_by_name("nonexistent") is None


class TestContextIntegration:
    """Test build_pattern_context with feedback + patterns."""

    def test_feedback_rendered_first(self):
        from agent_team.agents.context import build_pattern_context
        result = build_pattern_context(
            feedback=[{"rule": "Use type hints", "rationale": "User preference"}],
            patterns=[{"description": "Check imports", "category": "coding", "confidence": 0.8}],
        )
        fb_pos = result.index("User feedback")
        pat_pos = result.index("Auto-learned")
        assert fb_pos < pat_pos, "Feedback should appear before patterns"

    def test_empty_feedback_and_patterns(self):
        from agent_team.agents.context import build_pattern_context
        result = build_pattern_context(feedback=[], patterns=[])
        assert result == ""

    def test_feedback_only(self):
        from agent_team.agents.context import build_pattern_context
        result = build_pattern_context(feedback=[{"rule": "Test rule", "rationale": "R"}])
        assert "Test rule" in result
        assert "AUTO" not in result.upper() or "Auto-learned" not in result

    def test_patterns_only_backward_compat(self):
        from agent_team.agents.context import build_pattern_context
        result = build_pattern_context(
            patterns=[{"description": "Some pattern", "category": "bug", "confidence": 0.7}],
        )
        assert "Some pattern" in result
