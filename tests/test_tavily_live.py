"""Live Tavily API tests — requires network + TAVILY_API_KEY in .env.

Run with: pytest tests/test_tavily_live.py -v --timeout=120
These tests make real API calls and spawn the Tavily MCP server via npx.
"""
from __future__ import annotations

import asyncio
import os
import pytest

from agent_team.mcp.tavily_config import has_web_search
from agent_team.mcp.sanitizer import sanitize_web_result
from agent_team.mcp.tool_executor import parse_tool_calls


# Skip entire module if no Tavily key
pytestmark = pytest.mark.skipif(
    not has_web_search(),
    reason="TAVILY_API_KEY not set — skipping live tests",
)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_tavily_client():
    """Create an MCPStdioClient for the Tavily server."""
    from agent_team.mcp.config import MCPServerDef
    from agent_team.mcp.client import MCPStdioClient

    server_def = MCPServerDef(
        name="tavily",
        type="stdio",
        command="npx",
        args=["-y", "tavily-mcp@latest"],
        env={"TAVILY_API_KEY": "${TAVILY_API_KEY}"},
    )
    return MCPStdioClient(server_def)


class TestTavilyServerSpawn:
    """2.1 — Tavily MCP server spawns and connects."""

    def test_server_connects_and_lists_tools(self):
        """Spawn Tavily MCP server, initialize, and list tools."""
        client = _make_tavily_client()

        async def _test():
            connected = await client.connect()
            assert connected, "Failed to connect to Tavily MCP server"
            try:
                tools = client.get_tools()
                tool_names = [t.name for t in tools]
                assert len(tools) > 0, "No tools discovered"
                # At minimum, tavily_search should exist
                assert "tavily_search" in tool_names, f"tavily_search not in {tool_names}"
            finally:
                await client.disconnect()

        run_async(_test())


class TestTavilySearch:
    """2.2 — Real search query returns results."""

    def test_search_returns_content(self):
        """Call tavily_search with a real query and get results."""
        client = _make_tavily_client()

        async def _test():
            connected = await client.connect()
            assert connected
            try:
                result = await client.call_tool("tavily_search", {
                    "query": "Python programming language latest version",
                })
                assert not result.is_error, f"Search failed: {result.content}"
                assert len(result.content) > 0, "Empty search result"
                # Should contain something about Python
                assert "python" in result.content.lower() or "Python" in result.content
            finally:
                await client.disconnect()

        run_async(_test())


class TestTavilySanitization:
    """2.3 — Search results are properly sanitized."""

    def test_real_results_pass_sanitizer(self):
        """Real Tavily results survive sanitization without data loss."""
        client = _make_tavily_client()

        async def _test():
            connected = await client.connect()
            assert connected
            try:
                result = await client.call_tool("tavily_search", {
                    "query": "what is the capital of France",
                })
                raw = result.content
                sanitized = sanitize_web_result(raw)

                # Should be wrapped
                assert "WEB_SEARCH_RESULT (UNTRUSTED" in sanitized
                assert "END WEB_SEARCH_RESULT" in sanitized

                # Content should still be meaningful
                # (Paris or France should appear somewhere)
                lower = sanitized.lower()
                assert "paris" in lower or "france" in lower, \
                    f"Meaningful content lost after sanitization: {sanitized[:200]}..."

                # No tool call spoofing
                calls = parse_tool_calls(sanitized)
                assert len(calls) == 0, "Sanitized results should not parse as tool calls"
            finally:
                await client.disconnect()

        run_async(_test())


class TestTavilyByteBudget:
    """2.4 — Results fit within configured byte budget."""

    def test_result_within_budget(self):
        """Sanitized results respect the total_bytes budget."""
        client = _make_tavily_client()

        async def _test():
            connected = await client.connect()
            assert connected
            try:
                result = await client.call_tool("tavily_search", {
                    "query": "comprehensive history of artificial intelligence machine learning deep learning",
                })
                # Sanitize with default budget (3KB)
                sanitized = sanitize_web_result(result.content, total_bytes=3000)
                content_bytes = len(sanitized.encode("utf-8"))
                # Allow some overhead for the fence wrapper
                assert content_bytes < 4000, \
                    f"Sanitized content exceeds budget: {content_bytes} bytes"
            finally:
                await client.disconnect()

        run_async(_test())


class TestTavilyProviderDetection:
    """2.5 — Tavily tools are correctly detected as websearch provider."""

    def test_real_tools_detected_as_websearch(self):
        """Tools from actual Tavily server route to websearch provider."""
        from agent_team.mcp.providers import detect_provider
        client = _make_tavily_client()

        async def _test():
            connected = await client.connect()
            assert connected
            try:
                tools = client.get_tools()
                provider = detect_provider(tools)
                assert provider is not None, "No provider detected for Tavily tools"
                assert provider.name == "websearch", \
                    f"Expected 'websearch' provider, got '{provider.name}'"
            finally:
                await client.disconnect()

        run_async(_test())
