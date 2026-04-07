"""Agent team runner -- orchestrates agents through mode-specific pipelines."""
import asyncio
import json
import re
from fastapi import WebSocket, WebSocketDisconnect

from agent_team.config import (
    MAX_FIX_LOOPS, MAX_TOOL_ROUNDS, MAX_CONTEXT_TOKENS, REPO_ROOT,
    MODEL_ROUTING, SIMPLE_MODEL_ROUTING, MEDIUM_MODEL_ROUTING,
    DISCUSSION_MAX_OUTPUT_TOKENS, MAX_SUBAGENTS_PER_AGENT,
    SUBAGENT_MAX_INPUT_TOKENS, SUBAGENT_MAX_OUTPUT_TOKENS, FAST_MODEL,
)
from agent_team.agents.definitions import (
    AgentMode, AGENT_COLORS, MODE_PHASE_ORDER, SIMPLE_PHASE_ORDER, MODE_TEMPERATURES,
    MEDIUM_PHASE_ORDER, COMPLEX_PHASE_ORDER,
    AGENT_REGISTRY_MAP, SYNTHESIS_PROMPT, HANDOFF_FORMAT, RELOOP_TARGETS,
    SUBAGENT_INSTRUCTION,
    get_agent_prompt, CONTEXT_AGENTS,
    DEBATE_CHALLENGER_PROMPT, DEBATE_RESPONSE_PROMPT,
)
from agent_team.agents.context import build_context_for_agent, build_pattern_context
from agent_team.agents.complexity import TaskComplexity, classify_complexity
from agent_team.llm import stream_llm, call_llm, SessionTokenTracker, get_active_model, set_active_model
from agent_team.files.writer import extract_and_write_files, extract_run_commands, _resolve_base_dir
from agent_team.files.scaffolder import scaffold_plan_paths
from agent_team.plans.storage import save_plan_markdown


def _parse_column_names_from_description(description: str) -> list[str]:
    """Extract column names from a db_describe_table markdown table output.

    Expected format:
    | Column | Type | Nullable | Key | Default |
    |--------|------|----------|-----|---------|
    | user_id | INTEGER | NOT NULL | PK | |
    """
    columns: list[str] = []
    for line in description.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if cells and cells[0].lower() not in (
            "column", "name", "field", "col",
        ):
            columns.append(cells[0])
    return columns


class _LockedWebSocket:
    """Wrapper that serializes send_json calls for parallel-safe WebSocket writes."""

    def __init__(self, ws: WebSocket, lock: asyncio.Lock):
        self._ws = ws
        self._lock = lock

    async def send_json(self, data: dict) -> None:
        async with self._lock:
            await self._ws.send_json(data)

    async def receive_text(self) -> str:
        return await self._ws.receive_text()

    async def close(self, *args, **kwargs):
        await self._ws.close(*args, **kwargs)


class AgentTeam:
    def __init__(
        self,
        ws: WebSocket,
        execution_path: str | None = None,
        plan_only: bool = False,
        reuse_plan: bool = False,
        prior_phase_outputs: dict[str, str] | None = None,
    ):
        self._raw_ws = ws
        self._ws_lock = asyncio.Lock()  # Protects ws.send_json for parallel agents
        self.ws = _LockedWebSocket(ws, self._ws_lock)
        self.execution_path = execution_path
        self.plan_only = plan_only
        self.reuse_plan = reuse_plan
        self.prior_phase_outputs = prior_phase_outputs or {}
        self.phase_outputs: dict[str, str] = {}
        self.fix_loop_count = 0
        self.reloop_count = 0
        self.original_plan = ""
        self.mode = AgentMode.CODING
        self.complexity = TaskComplexity.MEDIUM
        self.memory_context = ""
        self.patterns_context = ""
        self.injected_pattern_ids: list[str] = []
        self.session_context = ""
        self.mcp_tools_prompt = ""
        self.mcp_registry = None
        self.token_tracker = SessionTokenTracker()
        self._model_overrides: dict[str, str] = {}  # Fallback overrides for unavailable models

    async def send_status(self, message: str, phase: str = ""):
        await self.ws.send_json({
            "type": "status",
            "message": message,
            "phase": phase,
        })

    # ── Generic MCP auto-discovery & auto-execution ──────────────────────────
    #
    # These replace the old database-specific functions with a capability-based
    # system that works for ANY MCP server (database, filesystem, API, etc.).

    def _get_server_connection_args(self, server_name: str) -> dict:
        """Read connection config for a specific MCP server from its env.

        Checks the server's env for a config path (e.g. DB_CONFIG_PATH),
        loads it, and returns the default connection profile.  Returns
        empty dict if no config is found — the server will use its default.
        """
        from pathlib import Path as _Path
        import json as _json

        if not self.mcp_registry:
            return {}

        server_def = self.mcp_registry.config.servers.get(server_name)
        if not server_def:
            return {}

        # Look for *_CONFIG_PATH or *_CONNECTION env vars
        cfg_path = None
        for key, val in server_def.env.items():
            if "CONFIG" in key.upper() or "CONNECTION" in key.upper():
                cfg_path = _Path(val).expanduser()
                break

        if not cfg_path:
            return {}

        try:
            if cfg_path.exists():
                cfg = _json.loads(cfg_path.read_text())
                conn = cfg.get("default_connection", "")
                if not conn and cfg.get("profiles"):
                    conn = next(iter(cfg["profiles"]))
                if conn:
                    return {"connection": conn}
        except Exception:
            pass
        return {}

    async def _auto_discover_context(self):
        """Auto-discover resources from ALL connected MCP servers.

        For each server with discovery tools, calls them to enumerate
        resources.  For each server with inspection tools, inspects the
        most relevant resources.  Results are injected into memory_context
        so agents have concrete data about the environment.

        Generic replacement for _auto_discover_schema() — works for any
        MCP server, not just databases.
        """
        if not self.mcp_registry:
            return

        caps_map = self.mcp_registry.get_capabilities()
        if not caps_map:
            return

        for server_name, caps in caps_map.items():
            if not caps.discovery_tools:
                continue

            # Check if user query matches this server's triggers
            server_def = self.mcp_registry.config.servers.get(server_name)
            triggers = server_def.triggers if server_def else []
            if triggers and not any(t in self.original_plan.lower() for t in triggers):
                continue

            await self.send_status(
                f"Discovering {server_name} resources...", "setup",
            )
            conn_args = self._get_server_connection_args(server_name)

            # Phase 1: Call discovery tools to enumerate resources
            context_parts: list[str] = []
            resource_names: list[str] = []
            for tool in caps.discovery_tools:
                result = await self.mcp_registry.call_tool(
                    server_name, tool.name, conn_args,
                )
                if not result or result.is_error:
                    continue
                context_parts.append(
                    f"## {tool.name}\n{result.content}"
                )
                # Extract resource names from output (markdown tables, lists)
                resource_names.extend(
                    self._extract_resource_names(result.content)
                )

            # Phase 2: Rank resources by relevance and inspect top-N
            described_resources: dict[str, str] = {}
            if caps.inspection_tools and resource_names:
                query_lower = (self.original_plan or "").lower()
                query_words = set(query_lower.split())

                def _relevance(name: str) -> int:
                    score = 0
                    nl = name.lower()
                    if nl in query_lower:
                        score += 100
                    if nl.rstrip("s") in query_lower or (nl + "s") in query_lower:
                        score += 50
                    nw = set(nl.replace("_", " ").split())
                    overlap = nw & query_words
                    if overlap:
                        score += len(overlap) * 10
                    return score

                scored = sorted(resource_names,
                                key=lambda r: (-_relevance(r), r))
                max_inspect = min(10, max(5, 20 - len(resource_names) // 5))

                for rname in scored[:max_inspect]:
                    for insp_tool in caps.inspection_tools:
                        args = self._build_inspection_args(
                            insp_tool, rname, conn_args,
                        )
                        result = await self.mcp_registry.call_tool(
                            server_name, insp_tool.name, args,
                        )
                        if result and not result.is_error:
                            context_parts.append(
                                f"## {rname}\n{result.content}"
                            )
                            described_resources[rname] = result.content

            # Phase 3: Discover relationships (FK / references)
            if resource_names:
                rel_ctx = await self._discover_relationships(
                    server_name, caps, resource_names, described_resources,
                    conn_args,
                )
                if rel_ctx:
                    context_parts.append(rel_ctx)

            # Inject discovered context into memory
            if context_parts:
                ctx = (
                    f"{server_name} resources (auto-discovered via MCP):\n\n"
                    + "\n\n".join(context_parts)
                )
                if self.memory_context:
                    self.memory_context = f"{ctx}\n\n{self.memory_context}"
                else:
                    self.memory_context = ctx
                await self.ws.send_json({
                    "type": "status",
                    "phase": "setup",
                    "message": (
                        f"Discovered: {len(resource_names)} resources from "
                        f"{server_name} ({', '.join(resource_names[:15])})"
                    ),
                })

    @staticmethod
    def _extract_resource_names(content: str) -> list[str]:
        """Extract resource names from MCP tool output.

        Handles common output formats:
        - Markdown tables: | name | ... |
        - Bullet lists: - name
        - Numbered lists: 1. name
        - Plain lines
        """
        names: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or "---" in line:
                continue
            # Markdown table row
            if line.startswith("|"):
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells and cells[0].replace("_", "").isalnum():
                    name = cells[0]
                    # Skip header rows
                    if name.lower() not in (
                        "table", "name", "table_name", "file", "resource",
                        "directory", "endpoint", "repo", "repository",
                    ):
                        names.append(name)
            # Bullet or numbered list
            elif line.startswith(("- ", "* ")) or (
                len(line) > 2 and line[0].isdigit() and line[1] in ".)"
            ):
                import re as _re
                m = _re.match(r'^(?:[-*]|\d+[.)]\s)\s*(.+)', line)
                if m:
                    name = m.group(1).strip().split()[0]  # First word
                    clean = name.replace("_", "").replace(".", "").replace(
                        "/", "").replace("-", "")
                    if clean and clean.isalnum():
                        names.append(name)
        # Remove duplicates, preserve order
        seen: set[str] = set()
        unique: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        return unique

    @staticmethod
    def _build_inspection_args(
        tool: object, resource_name: str, conn_args: dict,
    ) -> dict:
        """Build arguments for an inspection tool call.

        Uses the tool's input_schema to find the right parameter name
        for the resource identifier (e.g., 'table', 'path', 'repo').
        """
        args = dict(conn_args)
        schema = getattr(tool, "input_schema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        # Find the parameter that should receive the resource name
        # Prefer required params, then any non-connection param
        target_param = None
        for param_name in required:
            if param_name not in ("connection", "profile", "config"):
                target_param = param_name
                break
        if not target_param:
            for param_name in props:
                if param_name not in ("connection", "profile", "config"):
                    target_param = param_name
                    break
        if target_param:
            args[target_param] = resource_name
        return args

    # ── Relationship discovery ─────────────────────────────────────────────

    async def _discover_relationships(
        self,
        server_name: str,
        caps: object,
        resource_names: list[str],
        described_resources: dict[str, str],
        conn_args: dict,
    ) -> str:
        """Discover resource relationships generically.

        Uses the capabilities system — ZERO hardcoded DB logic:
        1. Try relationship_queries from capabilities (auto-detected or config)
        2. Fall back to column-name heuristic (works for any resource type)
        Returns formatted relationship context or empty string.
        """
        from agent_team.mcp.capabilities import find_query_param

        relationships: list[tuple[str, str, str, str]] = []

        # Try relationship queries from capabilities
        if caps.relationship_queries and caps.action_tools:
            action_tool = caps.action_tools[0]
            query_param = find_query_param(action_tool)
            if query_param:
                for query in caps.relationship_queries:
                    try:
                        result = await self.mcp_registry.call_tool(
                            server_name, action_tool.name,
                            {query_param: query, **conn_args},
                        )
                        if result and not result.is_error and result.content.strip():
                            relationships = self._parse_fk_result(result.content)
                            if relationships:
                                break
                    except Exception:
                        continue

        # Fallback: generic column-name heuristic
        if not relationships:
            relationships = self._infer_relationships_from_columns(
                resource_names, described_resources,
            )

        if not relationships:
            return ""

        lines = ["## Relationships (auto-discovered)"]
        for tbl, col, ref_tbl, ref_col in relationships:
            lines.append(f"- {tbl}.{col} → {ref_tbl}.{ref_col}")

        await self.send_status(
            f"Discovered {len(relationships)} relationships", "setup",
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_fk_result(content: str) -> list[tuple[str, str, str, str]]:
        """Parse FK query results from markdown table or pipe-separated output.

        Expects 4 columns: table, column, ref_table, ref_column.
        Handles both markdown table format and plain pipe-separated.
        """
        results: list[tuple[str, str, str, str]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) >= 4:
                # Skip header rows
                if cells[0].lower() in ("tbl", "table", "table_name"):
                    continue
                results.append((cells[0], cells[1], cells[2], cells[3]))
        return results

    @staticmethod
    def _infer_relationships_from_columns(
        resource_names: list[str],
        described_resources: dict[str, str],
    ) -> list[tuple[str, str, str, str]]:
        """Infer FK relationships from column naming patterns.

        For each column ending in '_id', checks if a matching table exists
        (singular or plural form).  Works even when FKs aren't declared.
        """
        table_set = {t.lower() for t in resource_names}
        relationships: list[tuple[str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        for table_name, description in described_resources.items():
            columns = _parse_column_names_from_description(description)
            for col in columns:
                col_lower = col.lower()
                if not col_lower.endswith("_id"):
                    continue
                base = col_lower[:-3]  # "user_id" → "user"
                # Try singular, plural-s, plural-es forms
                for candidate in [base, base + "s", base + "es"]:
                    if candidate in table_set and candidate != table_name.lower():
                        key = (table_name.lower(), col_lower)
                        if key not in seen:
                            seen.add(key)
                            relationships.append(
                                (table_name, col, candidate, col_lower)
                            )
                        break
        return relationships

    async def _auto_execute_from_output(self, agent_output: str):
        """Extract actionable content from agent output and auto-execute.

        Uses the capabilities system to determine what patterns to look for
        (SQL, file paths, URLs, etc.) and which tool to execute them with.
        Generic replacement for _auto_execute_db_queries().
        """
        if not self.mcp_registry:
            return

        from agent_team.mcp.capabilities import extract_content

        caps_map = self.mcp_registry.get_capabilities()
        exec_log: list[dict] = []

        for server_name, caps in caps_map.items():
            if not caps.action_tools or not caps.extract_patterns:
                continue

            conn_args = self._get_server_connection_args(server_name)

            for pattern_key in caps.extract_patterns:
                extracted = extract_content(agent_output, pattern_key)
                if not extracted:
                    continue

                # Find the action tool whose schema matches this pattern
                target_tool = None
                for tool in caps.action_tools:
                    props = tool.input_schema.get("properties", {})
                    for pname in props:
                        if pname.lower() in (pattern_key, "query", "sql",
                                             "path", "file", "url", "command"):
                            target_tool = (tool, pname)
                            break
                    if target_tool:
                        break

                if not target_tool:
                    continue

                tool, param_name = target_tool
                await self.send_status(
                    f"Executing {pattern_key} via {tool.name}...", "query",
                )

                for content in extracted[:5]:  # Max 5 per pattern
                    content = content.strip()
                    if not content:
                        continue
                    try:
                        args = {param_name: content, **conn_args}
                        result = await self.mcp_registry.call_tool(
                            server_name, tool.name, args,
                        )
                        exec_log.append({
                            "tool": tool.name,
                            "arguments": {param_name: content[:100],
                                          **conn_args},
                            "result": result.content[:3000] if result else "",
                            "is_error": result.is_error if result else True,
                        })
                    except Exception as exc:
                        exec_log.append({
                            "tool": tool.name,
                            "arguments": {param_name: content[:100]},
                            "result": str(exc)[:500],
                            "is_error": True,
                        })

        if exec_log:
            await self.ws.send_json({
                "type": "tool_results",
                "agent": "PLANNER",
                "tools_executed": exec_log,
            })

    def _swap_model_for_agent(self, agent_name: str) -> tuple[str | None, bool]:
        """Swap to the routed model for this agent. Returns (original_model, did_swap).

        .. deprecated:: Use _get_model_for_agent() + model_override instead.
        """
        if self.complexity == TaskComplexity.SIMPLE:
            routed_model = SIMPLE_MODEL_ROUTING.get(agent_name) or MODEL_ROUTING.get(agent_name)
        elif self.complexity == TaskComplexity.MEDIUM:
            routed_model = MEDIUM_MODEL_ROUTING.get(agent_name) or MODEL_ROUTING.get(agent_name)
        else:
            routed_model = MODEL_ROUTING.get(agent_name)
        if not routed_model:
            return None, False
        try:
            original = get_active_model()
            if routed_model != original:
                set_active_model(routed_model)
                return original, True
        except Exception:
            pass
        return None, False

    def _restore_model(self, original_model: str | None, did_swap: bool):
        """Restore the original model after agent run."""
        if did_swap and original_model:
            try:
                set_active_model(original_model)
            except Exception:
                pass

    def _get_model_for_agent(self, agent_name: str) -> str | None:
        """Resolve the routed model name for an agent without mutating global state."""
        if self.complexity == TaskComplexity.SIMPLE:
            model = SIMPLE_MODEL_ROUTING.get(agent_name) or MODEL_ROUTING.get(agent_name)
        elif self.complexity == TaskComplexity.MEDIUM:
            model = MEDIUM_MODEL_ROUTING.get(agent_name) or MODEL_ROUTING.get(agent_name)
        else:
            model = MODEL_ROUTING.get(agent_name)
        # Apply fallback overrides for models not available locally
        if model and model in self._model_overrides:
            return self._model_overrides[model]
        return model

    async def _validate_model_routing(self):
        """Check that all routed models are available in Ollama.

        If a model is not found, register a fallback override to the base MODEL.
        Only runs for the Ollama provider (local models).
        """
        try:
            from agent_team.llm.registry import get_active_provider
            provider = get_active_provider()
            if provider.name != "ollama":
                return

            available = await provider.list_models()
            if not available:
                return

            # Collect all unique models needed for the current routing
            if self.complexity == TaskComplexity.SIMPLE:
                routing = SIMPLE_MODEL_ROUTING
            elif self.complexity == TaskComplexity.MEDIUM:
                routing = MEDIUM_MODEL_ROUTING
            else:
                routing = MODEL_ROUTING

            needed = {v for v in routing.values() if v}
            base_model = MODEL

            for model in needed:
                if model not in available:
                    self._model_overrides[model] = base_model
                    await self.ws.send_json({
                        "type": "status",
                        "phase": "setup",
                        "message": (
                            f"Model '{model}' not found, "
                            f"falling back to '{base_model}'"
                        ),
                    })
        except Exception:
            pass

    async def run_agent(
        self, agent_name: str,
        intra_stage_outputs: dict[str, str] | None = None,
        extra_instruction: str = "",
    ) -> str:
        """Run a single agent and store its output."""
        system_prompt = get_agent_prompt(agent_name, self.mode, complexity=self.complexity.value)

        # For MEDIUM/COMPLEX, append handoff format instructions
        if self.complexity != TaskComplexity.SIMPLE and agent_name in AGENT_REGISTRY_MAP:
            system_prompt += "\n\n" + HANDOFF_FORMAT

        # For COMPLEX tasks, inject subagent capability
        if self.complexity == TaskComplexity.COMPLEX and agent_name in AGENT_REGISTRY_MAP:
            system_prompt += "\n\n" + SUBAGENT_INSTRUCTION

        # Inject MCP tool descriptions into system prompt for agents that can use them
        spec = AGENT_REGISTRY_MAP.get(agent_name)
        mcp_stages = ("orchestrator", "thinker", "planner", "executor")
        mcp_legacy = ("ORCHESTRATOR", "THINKER", "PLANNER", "EXECUTOR")
        if self.mcp_tools_prompt:
            if (spec and spec.stage in mcp_stages) or agent_name in mcp_legacy:
                system_prompt += "\n\n" + self.mcp_tools_prompt

        temperature = MODE_TEMPERATURES.get(self.mode, 0.3)
        messages = build_context_for_agent(
            agent_name, self.phase_outputs, self.original_plan,
            memory_context=self.memory_context,
            patterns_context=self.patterns_context,
            intra_stage_outputs=intra_stage_outputs,
        )

        display_name = spec.name if spec else agent_name
        proceed_msg = f"Please proceed as {display_name}."
        if extra_instruction:
            proceed_msg += f"\n\n{extra_instruction}"
        messages.append({"role": "user", "content": proceed_msg})

        # Model routing: resolve model for this agent (parallel-safe, no global mutation)
        routed_model = self._get_model_for_agent(agent_name)

        # --- Tool feedback loop: stream → tool calls → see results → reason → repeat ---
        from agent_team.mcp.tool_executor import execute_tool_calls

        tool_round = 0
        while True:
            if tool_round == 0:
                # Round 0: stream to UI as usual
                output = await stream_llm(
                    system_prompt=system_prompt,
                    messages=messages,
                    ws=self.ws,
                    agent_name=agent_name,
                    agent_color=AGENT_COLORS.get(agent_name, "#ffffff"),
                    temperature=temperature,
                    token_tracker=self.token_tracker,
                    display_name=display_name,
                    model_override=routed_model,
                )
            else:
                # Follow-up rounds: non-streaming for speed
                await self.send_status(
                    f"{display_name} processing tool results (round {tool_round})...",
                    "tool_feedback",
                )
                output = await call_llm(
                    system_prompt=system_prompt,
                    messages=messages,
                    temperature=temperature,
                    model_override=routed_model,
                )

            # Check for tool calls — exit loop if none
            if not (self.mcp_registry and "TOOL_CALL:" in output):
                break

            tool_round += 1
            if tool_round > MAX_TOOL_ROUNDS:
                break  # Safety cap

            try:
                updated_output, exec_log = await execute_tool_calls(
                    output, self.mcp_registry,
                )
                if not exec_log:
                    break
                output = updated_output
                await self.ws.send_json({
                    "type": "tool_results",
                    "agent": agent_name,
                    "tools_executed": exec_log,
                })
                # Feed tool results back to LLM as conversation continuation
                messages.append({"role": "assistant", "content": output})
                messages.append({"role": "user", "content": (
                    "Tool results are embedded above as TOOL_RESULT blocks. "
                    "Review the results and continue your analysis. "
                    "Make additional tool calls if needed, or provide your final output."
                )})
                # Token budget guard
                total_tokens = sum(
                    len(m.get("content", "")) // 4 for m in messages
                )
                if total_tokens > MAX_CONTEXT_TOKENS:
                    break
            except Exception:
                break
        # --- End tool feedback loop ---

        # Execute subagent requests (COMPLEX tasks only, max 1 per agent)
        if self.complexity == TaskComplexity.COMPLEX:
            subagent_tasks = self._parse_subagent_requests(output)
            if subagent_tasks:
                task = subagent_tasks[0]  # Limit to 1
                await self.send_status(
                    f"Subagent researching for {display_name}: {task['task'][:60]}...",
                    "subagent",
                )
                sub_result = await self._run_subagent(agent_name, task)
                # Let the agent integrate subagent results
                output = await stream_llm(
                    system_prompt=system_prompt,
                    messages=messages + [
                        {"role": "assistant", "content": output},
                        {"role": "user", "content": (
                            f"Your subagent research is complete:\n\n"
                            f"**Task:** {task['task']}\n"
                            f"**Results:**\n{sub_result}\n\n"
                            f"Integrate these findings into your final analysis."
                        )},
                    ],
                    ws=self.ws,
                    agent_name=f"{agent_name}_INTEGRATE",
                    agent_color=AGENT_COLORS.get(agent_name, "#ffffff"),
                    temperature=temperature,
                    token_tracker=self.token_tracker,
                    display_name=f"{display_name} (integrating)",
                    model_override=routed_model,
                )

        self.phase_outputs[agent_name] = output

        # Send agent output for session tracking
        await self.ws.send_json({
            "type": "agent_output",
            "agent": agent_name,
            "content": output[:2000],  # Cap to avoid huge payloads
        })

        return output

    def _parse_subagent_requests(self, output: str) -> list[dict]:
        """Parse ---SUBAGENT_REQUEST--- blocks from agent output."""
        requests = []
        for m in re.finditer(
            r"---SUBAGENT_REQUEST---\s*\n(.*?)---END_SUBAGENT_REQUEST---",
            output, re.DOTALL,
        ):
            block = m.group(1).strip()
            task = {}
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("task:"):
                    task["task"] = line.split(":", 1)[1].strip()
                elif line.startswith("focus:"):
                    task["focus"] = line.split(":", 1)[1].strip()
            if task.get("task"):
                requests.append(task)
                if len(requests) >= MAX_SUBAGENTS_PER_AGENT:
                    break
        return requests

    async def _run_subagent(self, parent_agent: str, task: dict) -> str:
        """Run a lightweight subagent — uses fast model, no streaming, no tools."""
        spec = AGENT_REGISTRY_MAP.get(parent_agent)
        parent_name = spec.name if spec else parent_agent
        focus = task.get("focus", task["task"])

        result = await call_llm(
            system_prompt=(
                f"You are a focused research assistant working for {parent_name}. "
                f"Your job: {focus}\n\n"
                f"Be concise and factual. Return only the relevant findings."
            ),
            messages=[{"role": "user", "content": task["task"]}],
            temperature=0.2,
            model_override=FAST_MODEL,
        )
        return result

    def needs_user_input(self, output: str) -> str | None:
        match = re.search(r"WAITING_FOR_USER:\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        return match.group(1).strip() if match else None

    def needs_fix(self, output: str) -> list[str]:
        fixes = []
        for marker in ("FIX_REQUIRED:", "REVISION_REQUIRED:"):
            match = re.search(rf"{marker}\n(.*?)(?:\n\u2192|\Z)", output, re.DOTALL)
            if match:
                for line in match.group(1).strip().split("\n"):
                    line = line.strip().lstrip("- ")
                    if line:
                        fixes.append(line)
        return fixes

    async def handle_user_question(self, output: str, agent_name: str) -> bool:
        """Check if agent needs user input and handle it. Returns True if handled."""
        question = self.needs_user_input(output)
        if question:
            await self.ws.send_json({
                "type": "waiting_for_user",
                "question": question,
                "agent": agent_name,
            })
            user_reply = await self.ws.receive_text()
            data = json.loads(user_reply)
            self.phase_outputs[agent_name] += f"\n\nUser clarification: {data['content']}"
            await self.send_status("Got it! Reprocessing with your input...", "intake")
            await self.run_agent(agent_name)
            return True
        return False

    # -- Multi-agent stage methods (MEDIUM/COMPLEX) ----------------------------

    def _get_stage_name(self, agent_ids: list[str]) -> str:
        """Determine the stage name from agent IDs."""
        for aid in agent_ids:
            spec = AGENT_REGISTRY_MAP.get(aid)
            if spec:
                return spec.stage
        # Legacy agent — infer stage from name
        first = agent_ids[0]
        for stage in ("orchestrator", "thinker", "planner", "executor", "reviewer"):
            if stage.upper() in first.upper():
                return stage
        return "unknown"

    async def run_stage(self, agent_ids: list[str], stage_name: str) -> dict[str, str]:
        """Run a pipeline stage with 1+ agents.

        Parallel think → discuss → synthesis model:
        - Phase 1: All agents run in parallel, independently
        - Phase 2: All agents see everyone's Phase 1 output, discuss in parallel
        - Phase 3: Lead agent synthesizes all discussion outputs
        For single-agent stages, runs directly without discussion.
        """
        phase_label = {
            "orchestrator": "Understanding your request",
            "thinker": "Deep analysis",
            "planner": "Planning",
            "executor": "Producing output",
            "reviewer": "Quality review",
        }.get(stage_name, stage_name.title())
        agent_names = ', '.join(
            AGENT_REGISTRY_MAP[a].name for a in agent_ids if a in AGENT_REGISTRY_MAP
        )
        await self.send_status(f"{phase_label} ({agent_names})...", stage_name)

        if len(agent_ids) == 1:
            output = await self.run_agent(agent_ids[0])
            # Check if agent needs user input before proceeding
            await self.handle_user_question(output, agent_ids[0])
            output = self.phase_outputs.get(agent_ids[0], output)
            self.phase_outputs[f"STAGE_{stage_name.upper()}"] = output
            return {agent_ids[0]: output}

        # === Phase 1: Parallel independent thinking ===
        await self.send_status(
            f"{phase_label} — parallel thinking ({agent_names})...", stage_name,
        )

        async def _run_think(aid: str) -> tuple[str, str]:
            out = await self.run_agent(aid)
            return aid, out

        think_results = await asyncio.gather(*[_run_think(aid) for aid in agent_ids])
        think_outputs: dict[str, str] = {aid: out for aid, out in think_results}

        # Handle user questions sequentially after parallel phase completes
        for aid, out in think_outputs.items():
            await self.handle_user_question(out, aid)
            think_outputs[aid] = self.phase_outputs.get(aid, out)

        # === Phase 2: Parallel discussion — each agent sees all Phase 1 outputs ===
        await self.send_status(
            f"{phase_label} — discussion round ({agent_names})...", stage_name,
        )

        all_perspectives = "\n\n---\n\n".join(
            f"[{AGENT_REGISTRY_MAP[aid].name}]:\n{out}"
            for aid, out in think_outputs.items()
        )

        async def _run_discuss(aid: str) -> tuple[str, str]:
            out = await self.run_agent(
                aid,
                extra_instruction=(
                    "Your colleagues have completed their independent analysis. "
                    "Review all perspectives below. Be concise — only address "
                    "disagreements, gaps, and strengthen the strongest ideas.\n\n"
                    f"All perspectives:\n{all_perspectives}"
                ),
            )
            return aid, out

        discuss_results = await asyncio.gather(
            *[_run_discuss(aid) for aid in agent_ids],
        )
        discussion_outputs: dict[str, str] = {aid: out for aid, out in discuss_results}

        # === Phase 3: Synthesis ===
        synth = await self._synthesize_stage(agent_ids[0], discussion_outputs, stage_name)
        self.phase_outputs[f"STAGE_{stage_name.upper()}"] = synth
        return discussion_outputs

    async def _synthesize_stage(
        self, lead_agent_id: str, outputs: dict[str, str], stage_name: str,
    ) -> str:
        """Synthesize multiple agent outputs into a single stage output."""
        spec = AGENT_REGISTRY_MAP.get(lead_agent_id)
        if not spec:
            # Fallback: just concatenate
            return "\n\n".join(outputs.values())

        await self.send_status(f"Synthesizing {stage_name} perspectives...", stage_name)

        # Build the perspectives text
        perspectives = []
        for aid, out in outputs.items():
            s = AGENT_REGISTRY_MAP.get(aid)
            name = s.name if s else aid
            perspectives.append(f"[{name}]:\n{out}")
        perspectives_text = "\n\n---\n\n".join(perspectives)

        synth_output = await self.run_agent(
            lead_agent_id,
            extra_instruction=(
                f"{SYNTHESIS_PROMPT}\n\n"
                f"The individual perspectives:\n\n{perspectives_text}"
            ),
        )
        return synth_output

    def _parse_handoff(self, output: str) -> dict | None:
        """Parse structured handoff block from stage output."""
        m = re.search(
            r"---HANDOFF---\s*\n(.*?)---END_HANDOFF---",
            output,
            re.DOTALL,
        )
        if not m:
            return None
        block = m.group(1)
        result: dict = {"status": "pass", "flags": [], "questions_for_user": []}
        for line in block.strip().splitlines():
            line = line.strip()
            if line.startswith("status:"):
                result["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("flags:"):
                val = line.split(":", 1)[1].strip().strip("[]")
                result["flags"] = [f.strip() for f in val.split(",") if f.strip()]
            elif line.startswith("questions_for_user:"):
                val = line.split(":", 1)[1].strip().strip("[]")
                result["questions_for_user"] = [q.strip() for q in val.split(",") if q.strip()]
        return result

    async def _handle_stage_handoff(self, stage_name: str) -> bool:
        """Check handoff status after a stage. Returns True if pipeline should re-loop."""
        stage_key = f"STAGE_{stage_name.upper()}"
        output = self.phase_outputs.get(stage_key, "")
        handoff = self._parse_handoff(output)

        # Always show handoff status in CLI
        status = handoff["status"] if handoff else "pass"
        flags = handoff.get("flags", []) if handoff else []
        flag_text = f" (flags: {', '.join(flags)})" if flags else ""
        await self.ws.send_json({
            "type": "status",
            "phase": "handoff",
            "message": f"✅ {stage_name.title()} → PASS{flag_text}" if status == "pass"
                       else f"🚫 {stage_name.title()} → BLOCKED{flag_text}",
        })

        if not handoff or status == "pass":
            return False

        # Blocked — check for user questions
        questions = handoff.get("questions_for_user", [])
        if questions:
            question_text = "\n".join(f"- {q}" for q in questions)
            await self.ws.send_json({
                "type": "waiting_for_user",
                "question": f"The {stage_name} stage needs clarification:\n{question_text}",
                "agent": stage_name,
            })
            user_reply = await self.ws.receive_text()
            data = json.loads(user_reply)
            self.phase_outputs[stage_key] += f"\n\nUser clarification: {data['content']}"
            return True  # Re-run this stage

        # Blocked without questions — re-loop to target stage
        target = RELOOP_TARGETS.get(stage_name)
        if target and self.reloop_count < MAX_FIX_LOOPS:
            self.reloop_count += 1
            flags = ", ".join(handoff.get("flags", ["unspecified"]))
            await self.send_status(
                f"{stage_name.title()} blocked ({flags}) — looping back to {target}...",
                stage_name,
            )
            return True  # Caller handles re-loop
        return False

    # -- Legacy single-agent stage handlers (used by SIMPLE tasks) -------------

    async def run_orchestrator(self):
        await self.send_status(f"Phase 1: Understanding your request ({self.mode.value} mode)...", "intake")
        output = await self.run_agent("ORCHESTRATOR")
        await self.handle_user_question(output, "ORCHESTRATOR")

    async def run_thinker(self):
        await self.send_status("Phase 2: Deep analysis...", "think")
        await self.run_agent("THINKER")

    async def run_debate(self):
        """Run a debate between THINKER and CHALLENGER for higher accuracy."""
        await self.send_status("Phase 2b: Agent debate — challenging analysis...", "debate")

        # CHALLENGER reviews THINKER's output
        thinker_output = self.phase_outputs.get("THINKER", "")
        if not thinker_output:
            return

        # Run challenger with model_override (parallel-safe)
        temperature = MODE_TEMPERATURES.get(self.mode, 0.3)
        challenge_messages = [
            {"role": "user", "content": self.original_plan},
            {"role": "assistant", "content": thinker_output},
            {"role": "user", "content": "Please critically review the above analysis. Find weaknesses, gaps, and suggest improvements."},
        ]

        challenger_output = await stream_llm(
            system_prompt=DEBATE_CHALLENGER_PROMPT,
            messages=challenge_messages,
            ws=self.ws,
            agent_name="CHALLENGER",
            agent_color="#ff6b6b",
            temperature=temperature + 0.1,
            token_tracker=self.token_tracker,
            model_override=self._get_model_for_agent("CHALLENGER"),
        )
        self.phase_outputs["CHALLENGER"] = challenger_output

        await self.ws.send_json({
            "type": "agent_output",
            "agent": "CHALLENGER",
            "content": challenger_output[:2000],
        })

        # THINKER responds to challenges
        await self.send_status("Phase 2c: Refining analysis based on debate...", "debate")

        response_messages = [
            {"role": "user", "content": self.original_plan},
            {"role": "assistant", "content": thinker_output},
            {"role": "user", "content": f"A critical reviewer has raised these challenges:\n\n{challenger_output}\n\nPlease respond to each challenge and produce a refined analysis."},
        ]

        refined_output = await stream_llm(
            system_prompt=DEBATE_RESPONSE_PROMPT,
            messages=response_messages,
            ws=self.ws,
            agent_name="THINKER_REFINED",
            agent_color="#c084fc",
            temperature=temperature,
            token_tracker=self.token_tracker,
            model_override=self._get_model_for_agent("THINKER_REFINED"),
        )

        # Replace THINKER output with refined version
        self.phase_outputs["THINKER"] = refined_output

        await self.ws.send_json({
            "type": "agent_output",
            "agent": "THINKER_REFINED",
            "content": refined_output[:2000],
        })

    async def run_planner(self):
        await self.send_status("Phase 3: Planning...", "plan")
        output = await self.run_agent("PLANNER")
        await self.handle_user_question(output, "PLANNER")

        # Save plan
        first_line = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        title = first_line[:100] if first_line else "Agent Team Plan"
        save_plan_markdown(title=title, plan_text=output,
                          execution_path=self.execution_path, mode=self.mode.value)

        # Scaffold paths (only in coding/execution modes, skip during plan_only)
        if self.mode in (AgentMode.CODING, AgentMode.EXECUTION) and not self.plan_only:
            await self.send_status("Checking plan paths...", "plan")
            created, existing = scaffold_plan_paths(output, execution_path=self.execution_path)
            if existing:
                existing_list = "\n".join(f"  - {p}" for p in existing)
                await self.ws.send_json({
                    "type": "waiting_for_user",
                    "question": f"These files exist:\n{existing_list}\n\noverwrite / skip / abort?",
                    "agent": "PLANNER",
                })
                user_reply = await self.ws.receive_text()
                data = json.loads(user_reply)
                choice = data.get("content", "overwrite").strip().lower()
                if choice.startswith("abort"):
                    await self.ws.send_json({"type": "complete", "message": "Aborted."})
                    raise WebSocketDisconnect()
                self.phase_outputs["_existing_file_choice"] = choice
            if created:
                await self.send_status(f"Scaffolded {len(created)} path(s).", "plan")

        # Auto-execute SQL if MCP db tools are available
        if self.mcp_registry:
            await self._auto_execute_from_output(output)

    async def _write_executor_files(self, executor_output: str):
        """Write files from executor output to disk (shared by legacy and named pipeline)."""
        if self.mode not in (AgentMode.CODING, AgentMode.EXECUTION):
            return
        skip_existing = self.phase_outputs.get("_existing_file_choice", "overwrite").startswith("skip")
        changes = extract_and_write_files(
            executor_output, execution_path=self.execution_path,
            skip_existing=skip_existing,
            planner_output=self.phase_outputs.get("PLANNER", "") or self.phase_outputs.get("STAGE_PLANNER", ""),
        )
        if not changes:
            await self.send_status(
                "Warning: EXECUTOR output did not contain recognizable file blocks.",
                "execute",
            )
        if changes:
            base = _resolve_base_dir(self.execution_path)
            await self.ws.send_json({
                "type": "file_changes",
                "files": [
                    {"path": str(c.path), "is_new": c.is_new, "diff": c.diff, "preview": c.preview}
                    for c in changes
                ],
                "base_dir": str(base),
            })
            await self.send_status(f"Wrote {len(changes)} file(s).", "execute")

        if self.mode == AgentMode.EXECUTION:
            commands = extract_run_commands(executor_output)
            if commands:
                await self.send_status("Running commands...", "execute")
                from agent_team.security.sandbox import SandboxExecutor
                sandbox = SandboxExecutor(workspace=_resolve_base_dir(self.execution_path))
                results = []
                for desc, cmd in commands:
                    await self.send_status(f"Executing: {desc}", "execute")
                    stdout, stderr, code = await sandbox.execute(cmd)
                    results.append(f"## {desc}\nExit code: {code}\nStdout: {stdout}\nStderr: {stderr}")
                self.phase_outputs["EXECUTION_RESULTS"] = "\n\n".join(results)

    async def run_executor(self):
        """Legacy executor handler for SIMPLE pipeline."""
        await self.send_status("Phase 4: Producing output...", "execute")
        executor_output = await self.run_agent("EXECUTOR")
        await self._write_executor_files(executor_output)

    async def run_reviewer(self):
        await self.send_status("Phase 5: Quality review...", "verify")
        reviewer_output = await self.run_agent("REVIEWER")

        # Check for fix requirements (only in coding/execution modes)
        if self.mode in (AgentMode.CODING, AgentMode.EXECUTION):
            fixes = self.needs_fix(reviewer_output)
            if fixes and self.fix_loop_count < MAX_FIX_LOOPS:
                self.fix_loop_count += 1
                await self.send_status(
                    f"Fixes needed -- loop {self.fix_loop_count}/{MAX_FIX_LOOPS}...", "verify"
                )
                # Capture original output for error learning
                original_executor = self.phase_outputs.get("EXECUTOR", "")

                fix_context = "FIXES NEEDED:\n" + "\n".join(f"- {f}" for f in fixes)
                self.phase_outputs["EXECUTOR"] = original_executor + f"\n\n{fix_context}"
                await self.run_executor()

                # Extract error patterns from this fix loop (best-effort)
                try:
                    from agent_team.learning.extractor import extract_error_patterns
                    await extract_error_patterns(
                        reviewer_output=reviewer_output,
                        executor_original=original_executor,
                        executor_fixed=self.phase_outputs.get("EXECUTOR", ""),
                        user_plan=self.original_plan,
                    )
                except Exception:
                    pass

                await self.run_reviewer()  # Re-review

    async def run(self, user_plan: str, mode: str = "coding"):
        """Main entry point -- run the full agent pipeline."""
        self.original_plan = user_plan
        try:
            self.mode = AgentMode(mode)
        except ValueError:
            self.mode = AgentMode.CODING

        try:
            # Pre-session: check MCP tools (skip remote tools for local LLMs)
            if self.mcp_registry and self.mcp_tools_prompt:
                try:
                    from agent_team.llm.registry import get_active_provider_name
                    provider = get_active_provider_name()
                    if provider in ("ollama", "huggingface"):
                        from agent_team.mcp.config import MCPConfig
                        config = self.mcp_registry.config
                        remote_servers = [
                            s.name for s in config.list_servers() if s.is_remote
                        ]
                        if remote_servers:
                            await self.ws.send_json({
                                "type": "status",
                                "message": f"Skipping remote MCP servers ({', '.join(remote_servers)}) — "
                                           f"not supported with local LLM ({provider}). "
                                           f"Use a frontier LLM or download the server source code.",
                                "phase": "setup",
                            })
                except Exception:
                    pass

            # Pre-session: auto-discover resources from connected MCP servers.
            # Uses the capabilities system to call discovery/inspection tools
            # and inject results into agent context (works for any MCP server).
            if self.mcp_registry and self.mcp_tools_prompt:
                try:
                    await self._auto_discover_context()
                except Exception:
                    pass

            # Classify task complexity for adaptive routing
            self.complexity = classify_complexity(user_plan, self.mode.value)
            await self.send_status(
                f"Task classified as: {self.complexity.value}", "setup"
            )

            # Pre-session: query memory for relevant context
            try:
                from agent_team.memory.search import HybridSearch
                searcher = HybridSearch()
                results = await searcher.search(user_plan, top_k=5)
                if results:
                    self.memory_context = "\n\n".join(
                        f"[{r.source}] {r.content}" for r in results
                    )
                    await self.ws.send_json({
                        "type": "memory_context",
                        "results": [{"content": r.content, "score": r.score, "source": r.source} for r in results],
                    })
            except Exception:
                pass

            # Pre-session: query learned patterns for injection
            try:
                from agent_team.memory.database import MemoryDB
                _db = MemoryDB()
                patterns = _db.get_relevant_patterns(min_confidence=0.4, limit=10)
                if patterns:
                    self.patterns_context = build_pattern_context(patterns)
                    self.injected_pattern_ids = [p["id"] for p in patterns]
                _db.close()
            except Exception:
                pass

            # Inject session context
            if self.session_context:
                if self.memory_context:
                    self.memory_context = f"{self.session_context}\n\n{self.memory_context}"
                else:
                    self.memory_context = self.session_context

            # Select phase order based on complexity
            if self.complexity == TaskComplexity.SIMPLE:
                full_phase_order = list(SIMPLE_PHASE_ORDER.get(
                    self.mode, SIMPLE_PHASE_ORDER[AgentMode.CODING]
                ))
            elif self.complexity == TaskComplexity.MEDIUM:
                full_phase_order = list(MEDIUM_PHASE_ORDER.get(
                    self.mode, MEDIUM_PHASE_ORDER[AgentMode.CODING]
                ))
            else:  # COMPLEX
                full_phase_order = list(COMPLEX_PHASE_ORDER.get(
                    self.mode, COMPLEX_PHASE_ORDER[AgentMode.CODING]
                ))

            # A5: If reusing a prior plan, inject outputs and skip to EXECUTOR+REVIEWER
            _exec_rev_ids = {"EXECUTOR", "EXEC_KAI", "EXEC_DEV", "EXEC_SAGE",
                             "REVIEWER", "REV_QUINN", "REV_LENA"}
            if self.reuse_plan and self.prior_phase_outputs:
                self.phase_outputs.update(self.prior_phase_outputs)
                # Use executor+reviewer stages from the correct phase order
                phase_order = [g for g in full_phase_order
                               if _exec_rev_ids.intersection(g)]
                if not phase_order:
                    phase_order = [["EXECUTOR"], ["REVIEWER"]]
            elif self.plan_only:
                # A1: Skip EXECUTOR and REVIEWER when plan_only
                phase_order = [g for g in full_phase_order
                               if not _exec_rev_ids.intersection(g)]
            else:
                phase_order = full_phase_order

            # Log complexity classification to CLI
            await self.ws.send_json({
                "type": "status",
                "phase": "complexity",
                "message": f"Task complexity: {self.complexity.value.upper()} → {len(phase_order)} phases",
            })

            # Pre-session: validate model availability
            await self._validate_model_routing()

            # -- Main pipeline loop --
            _LEGACY_HANDLERS = {
                "ORCHESTRATOR": self.run_orchestrator,
                "THINKER": self.run_thinker,
                "PLANNER": self.run_planner,
                "EXECUTOR": self.run_executor,
                "REVIEWER": self.run_reviewer,
            }

            for phase_group in phase_order:
                is_named = any(a in AGENT_REGISTRY_MAP for a in phase_group)

                if is_named:
                    # Multi-agent pipeline (MEDIUM/COMPLEX)
                    stage_name = self._get_stage_name(phase_group)
                    await self.run_stage(phase_group, stage_name)

                    # Post-stage processing for named agents
                    if stage_name == "planner":
                        plan_out = self.phase_outputs.get(f"STAGE_PLANNER", "")
                        if plan_out:
                            self.phase_outputs["PLANNER"] = plan_out  # compat
                            self.original_plan_output = plan_out
                            if not self.plan_only:
                                scaffold_plan_paths(plan_out, execution_path=self.execution_path)
                            save_plan_markdown(
                                title=next((ln for ln in plan_out.splitlines() if ln.strip()), "Plan"),
                                plan_text=plan_out,
                                execution_path=self.execution_path,
                                mode=self.mode.value,
                            )
                            # Auto-execute SQL if MCP db tools are available
                            if self.mcp_registry:
                                await self._auto_execute_from_output(plan_out)
                    elif stage_name == "executor":
                        # Trigger file writing from executor output
                        exec_out = self.phase_outputs.get(f"STAGE_EXECUTOR", "")
                        if exec_out:
                            self.phase_outputs["EXECUTOR"] = exec_out  # compat
                            await self._write_executor_files(exec_out)

                    # Check handoff status
                    await self._handle_stage_handoff(stage_name)

                elif len(phase_group) == 1:
                    # Legacy single-agent (SIMPLE)
                    agent = phase_group[0]
                    handler = _LEGACY_HANDLERS.get(agent)
                    if handler:
                        await handler()
                    # Run debate after THINKER (skip for simple tasks)
                    if agent == "THINKER" and self.complexity != TaskComplexity.SIMPLE:
                        await self.run_debate()
                else:
                    # Legacy parallel group (shouldn't happen in SIMPLE, but just in case)
                    tasks = [_LEGACY_HANDLERS[a]() for a in phase_group if a in _LEGACY_HANDLERS]
                    if tasks:
                        await asyncio.gather(*tasks)

            # Post-session: boost/decay injected patterns based on outcome
            try:
                if self.injected_pattern_ids:
                    from agent_team.memory.database import MemoryDB
                    _db = MemoryDB()
                    had_fixes = self.fix_loop_count > 0
                    delta = 0.05 if not had_fixes else -0.05
                    for pid in self.injected_pattern_ids:
                        _db.boost_pattern_confidence(pid, delta)
                    _db.close()
            except Exception:
                pass

            # Post-session: trigger learning
            try:
                from agent_team.learning.extractor import extract_session_knowledge
                await extract_session_knowledge(
                    user_plan=user_plan,
                    mode=self.mode.value,
                    phase_outputs=self.phase_outputs,
                )
            except Exception:
                pass

            await self.ws.send_json({
                "type": "complete",
                "model": get_active_model(),
                "token_summary": self.token_tracker.summary(),
                "execution_path": self.execution_path,
            })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await self.ws.send_json({
                "type": "error",
                "content": f"Team error: {str(e)}",
            })
