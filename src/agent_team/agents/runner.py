"""Agent team runner -- orchestrates agents through mode-specific pipelines."""
import asyncio
import json
import re
from fastapi import WebSocket, WebSocketDisconnect

from agent_team.config import MAX_FIX_LOOPS, REPO_ROOT, THINKING_MODEL
from agent_team.agents.definitions import (
    AgentMode, AGENT_COLORS, MODE_PHASE_ORDER, MODE_TEMPERATURES,
    get_agent_prompt, CONTEXT_AGENTS,
    DEBATE_CHALLENGER_PROMPT, DEBATE_RESPONSE_PROMPT,
)
from agent_team.agents.context import build_context_for_agent
from agent_team.llm import stream_llm, SessionTokenTracker, get_active_model
from agent_team.files.writer import extract_and_write_files, extract_run_commands, _resolve_base_dir
from agent_team.files.scaffolder import scaffold_plan_paths
from agent_team.plans.storage import save_plan_markdown


class AgentTeam:
    def __init__(self, ws: WebSocket, execution_path: str | None = None):
        self.ws = ws
        self.execution_path = execution_path
        self.phase_outputs: dict[str, str] = {}
        self.fix_loop_count = 0
        self.original_plan = ""
        self.mode = AgentMode.CODING
        self.memory_context = ""
        self.session_context = ""
        self.mcp_tools_prompt = ""  # MCP tool descriptions to inject into prompts
        self.mcp_registry = None    # MCPRegistry reference for tool execution
        self.token_tracker = SessionTokenTracker()

    async def send_status(self, message: str, phase: str = ""):
        await self.ws.send_json({
            "type": "status",
            "message": message,
            "phase": phase,
        })

    async def run_agent(self, agent_name: str) -> str:
        """Run a single agent and store its output."""
        system_prompt = get_agent_prompt(agent_name, self.mode)

        # Inject MCP tool descriptions into system prompt for agents that can use them
        if self.mcp_tools_prompt and agent_name in ("THINKER", "PLANNER", "EXECUTOR"):
            system_prompt += "\n\n" + self.mcp_tools_prompt

        temperature = MODE_TEMPERATURES.get(self.mode, 0.3)
        messages = build_context_for_agent(
            agent_name, self.phase_outputs, self.original_plan,
            memory_context=self.memory_context,
        )
        messages.append({
            "role": "user",
            "content": f"Please proceed as {agent_name}.",
        })

        # Use thinking model for analysis agents (THINKER, PLANNER, REVIEWER)
        use_thinking_model = agent_name in ("THINKER", "PLANNER", "REVIEWER") and THINKING_MODEL
        original_model = None
        if use_thinking_model:
            try:
                original_model = get_active_model()
                from agent_team.llm import set_active_model
                set_active_model(THINKING_MODEL)
            except Exception:
                use_thinking_model = False

        try:
            output = await stream_llm(
                system_prompt=system_prompt,
                messages=messages,
                ws=self.ws,
                agent_name=agent_name,
                agent_color=AGENT_COLORS.get(agent_name, "#ffffff"),
                temperature=temperature,
                token_tracker=self.token_tracker,
            )
        finally:
            # Restore original model if we swapped it
            if use_thinking_model and original_model:
                from agent_team.llm import set_active_model
                set_active_model(original_model)

        # Execute any MCP tool calls found in the output
        if self.mcp_registry and "TOOL_CALL:" in output:
            try:
                from agent_team.mcp.tool_executor import execute_tool_calls
                updated_output, exec_log = await execute_tool_calls(output, self.mcp_registry)
                if exec_log:
                    output = updated_output
                    await self.ws.send_json({
                        "type": "tool_results",
                        "agent": agent_name,
                        "tools_executed": exec_log,
                    })
            except Exception:
                pass

        self.phase_outputs[agent_name] = output

        # Send agent output for session tracking
        await self.ws.send_json({
            "type": "agent_output",
            "agent": agent_name,
            "content": output[:2000],  # Cap to avoid huge payloads
        })

        return output

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

        # Use thinking model for debate if available
        original_model = None
        if THINKING_MODEL:
            try:
                original_model = get_active_model()
                from agent_team.llm import set_active_model
                set_active_model(THINKING_MODEL)
            except Exception:
                pass

        try:
            # Run challenger
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
            )

            # Replace THINKER output with refined version
            self.phase_outputs["THINKER"] = refined_output

            await self.ws.send_json({
                "type": "agent_output",
                "agent": "THINKER_REFINED",
                "content": refined_output[:2000],
            })
        finally:
            # Restore original model
            if original_model:
                from agent_team.llm import set_active_model
                set_active_model(original_model)

    async def run_planner(self):
        await self.send_status("Phase 3: Planning...", "plan")
        output = await self.run_agent("PLANNER")
        await self.handle_user_question(output, "PLANNER")

        # Save plan
        first_line = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        title = first_line[:100] if first_line else "Agent Team Plan"
        save_plan_markdown(title=title, plan_text=output,
                          execution_path=self.execution_path, mode=self.mode.value)

        # Scaffold paths (only in coding/execution modes)
        if self.mode in (AgentMode.CODING, AgentMode.EXECUTION):
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

    async def run_executor(self):
        await self.send_status("Phase 4: Producing output...", "execute")
        skip_existing = self.phase_outputs.get("_existing_file_choice", "overwrite").startswith("skip")
        executor_output = await self.run_agent("EXECUTOR")

        # Write files if in coding/execution mode
        if self.mode in (AgentMode.CODING, AgentMode.EXECUTION):
            written = extract_and_write_files(
                executor_output, execution_path=self.execution_path,
                skip_existing=skip_existing,
            )
            if written:
                base = _resolve_base_dir(self.execution_path)
                await self.send_status(f"Wrote {len(written)} file(s).", "execute")

        # Run commands if in execution mode
        if self.mode == AgentMode.EXECUTION:
            commands = extract_run_commands(executor_output)
            if commands:
                await self.send_status("Running commands...", "execute")
                # Import sandbox here to avoid circular imports
                from agent_team.security.sandbox import SandboxExecutor
                sandbox = SandboxExecutor(
                    workspace=_resolve_base_dir(self.execution_path)
                )
                results = []
                for desc, cmd in commands:
                    await self.send_status(f"Executing: {desc}", "execute")
                    stdout, stderr, code = await sandbox.execute(cmd)
                    results.append(f"## {desc}\nExit code: {code}\nStdout: {stdout}\nStderr: {stderr}")
                self.phase_outputs["EXECUTION_RESULTS"] = "\n\n".join(results)

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
                fix_context = "FIXES NEEDED:\n" + "\n".join(f"- {f}" for f in fixes)
                self.phase_outputs["EXECUTOR"] = self.phase_outputs.get("EXECUTOR", "") + f"\n\n{fix_context}"
                await self.run_executor()
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
                        # Local LLM — only include tools from stdio servers
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
                pass  # Memory not available yet, that's fine

            # Inject session context
            if self.session_context:
                if self.memory_context:
                    self.memory_context = f"{self.session_context}\n\n{self.memory_context}"
                else:
                    self.memory_context = self.session_context

            # Run the mode-specific pipeline
            phase_order = MODE_PHASE_ORDER.get(self.mode, MODE_PHASE_ORDER[AgentMode.CODING])
            for phase_group in phase_order:
                if len(phase_group) == 1:
                    agent = phase_group[0]
                    handler = {
                        "ORCHESTRATOR": self.run_orchestrator,
                        "THINKER": self.run_thinker,
                        "PLANNER": self.run_planner,
                        "EXECUTOR": self.run_executor,
                        "REVIEWER": self.run_reviewer,
                    }.get(agent)
                    if handler:
                        await handler()
                    # Run debate after THINKER for better accuracy
                    if agent == "THINKER":
                        await self.run_debate()
                else:
                    # Parallel execution
                    tasks = []
                    for agent in phase_group:
                        handler = {
                            "ORCHESTRATOR": self.run_orchestrator,
                            "THINKER": self.run_thinker,
                            "PLANNER": self.run_planner,
                            "EXECUTOR": self.run_executor,
                            "REVIEWER": self.run_reviewer,
                        }.get(agent)
                        if handler:
                            tasks.append(handler())
                    if tasks:
                        await asyncio.gather(*tasks)

            # Post-session: trigger learning
            try:
                from agent_team.learning.extractor import extract_session_knowledge
                await extract_session_knowledge(
                    user_plan=user_plan,
                    mode=self.mode.value,
                    phase_outputs=self.phase_outputs,
                )
            except Exception:
                pass  # Learning not critical

            await self.ws.send_json({
                "type": "complete",
                "model": get_active_model(),
                "token_summary": self.token_tracker.summary(),
            })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await self.ws.send_json({
                "type": "error",
                "content": f"Team error: {str(e)}",
            })
