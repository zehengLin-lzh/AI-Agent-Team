"""Tool executor — parse tool calls from LLM output and execute via MCP."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_team.mcp.sanitizer import sanitize_web_result
from agent_team.mcp.providers.websearch import WebSearchProvider

# Tool names that produce untrusted web content requiring sanitization
_WEB_TOOL_NAMES: set[str] = set(WebSearchProvider.tool_name_patterns)


@dataclass
class ParsedToolCall:
    """A tool call parsed from LLM output."""
    tool_name: str
    arguments: dict
    raw_block: str          # The full matched block for replacement


# Pattern for tool call blocks in LLM output
TOOL_CALL_PATTERN = re.compile(
    r'---\s*TOOL_CALL:\s*(\S+)\s*---\s*\n(.*?)\n---\s*END\s*TOOL_CALL\s*---',
    re.DOTALL,
)


def parse_tool_calls(text: str) -> list[ParsedToolCall]:
    """Extract tool call blocks from LLM-generated text."""
    calls = []
    for match in TOOL_CALL_PATTERN.finditer(text):
        tool_name = match.group(1).strip()
        args_str = match.group(2).strip()
        raw_block = match.group(0)

        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            # Try to be lenient — maybe it's not valid JSON
            arguments = {"raw_input": args_str}

        calls.append(ParsedToolCall(
            tool_name=tool_name,
            arguments=arguments,
            raw_block=raw_block,
        ))

    return calls


def inject_tool_results(text: str, call: ParsedToolCall, result_text: str) -> str:
    """Replace a tool call block with its result in the text."""
    replacement = (
        f"--- TOOL_RESULT: {call.tool_name} ---\n"
        f"{result_text}\n"
        f"--- END TOOL_RESULT ---"
    )
    return text.replace(call.raw_block, replacement, 1)


async def execute_tool_calls(text: str, registry) -> tuple[str, list[dict]]:
    """Parse all tool calls from text, execute them, and return updated text.

    Args:
        text: The LLM output text containing tool call blocks
        registry: MCPRegistry instance

    Returns:
        (updated_text, execution_log)
    """
    calls = parse_tool_calls(text)
    if not calls:
        return text, []

    execution_log = []
    updated_text = text

    for call in calls:
        result = await registry.call_tool_by_name(call.tool_name, call.arguments)

        # Sanitize untrusted web content before it enters the conversation
        content = result.content
        if call.tool_name in _WEB_TOOL_NAMES and not result.is_error:
            content = sanitize_web_result(content)

        updated_text = inject_tool_results(updated_text, call, content)
        execution_log.append({
            "tool": call.tool_name,
            "arguments": call.arguments,
            "result": content[:3000],
            "is_error": result.is_error,
        })

    return updated_text, execution_log
