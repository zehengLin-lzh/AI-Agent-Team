"""Agent team runner -- orchestrates agents through mode-specific pipelines."""
import asyncio
import json
import re
from fastapi import WebSocket, WebSocketDisconnect

from agent_team.config import (
    MAX_FIX_LOOPS, REPO_ROOT, MODEL_ROUTING, SIMPLE_MODEL_ROUTING, MEDIUM_MODEL_ROUTING,
)
from agent_team.agents.definitions import (
    AgentMode, AGENT_COLORS, MODE_PHASE_ORDER, SIMPLE_PHASE_ORDER, MODE_TEMPERATURES,
    MEDIUM_PHASE_ORDER, COMPLEX_PHASE_ORDER,
    AGENT_REGISTRY_MAP, SYNTHESIS_PROMPT, HANDOFF_FORMAT, RELOOP_TARGETS,
    get_agent_prompt, CONTEXT_AGENTS,
    DEBATE_CHALLENGER_PROMPT, DEBATE_RESPONSE_PROMPT,
)
from agent_team.agents.context import build_context_for_agent, build_pattern_context
from agent_team.agents.complexity import TaskComplexity, classify_complexity
from agent_team.llm import stream_llm, SessionTokenTracker, get_active_model, set_active_model
from agent_team.files.writer import extract_and_write_files, extract_run_commands, _resolve_base_dir
from agent_team.files.scaffolder import scaffold_plan_paths
from agent_team.plans.storage import save_plan_markdown


class AgentTeam:
    def __init__(
        self,
        ws: WebSocket,
        execution_path: str | None = None,
        plan_only: bool = False,
        reuse_plan: bool = False,
        prior_phase_outputs: dict[str, str] | None = None,
    ):
        self.ws = ws
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

    async def send_status(self, message: str, phase: str = ""):
        await self.ws.send_json({
            "type": "status",
            "message": message,
            "phase": phase,
        })

    def _swap_model_for_agent(self, agent_name: str) -> tuple[str | None, bool]:
        """Swap to the routed model for this agent. Returns (original_model, did_swap)."""
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

        # Model routing: swap to the configured model for this agent role
        original_model, did_swap = self._swap_model_for_agent(agent_name)

        try:
            output = await stream_llm(
                system_prompt=system_prompt,
                messages=messages,
                ws=self.ws,
                agent_name=agent_name,
                agent_color=AGENT_COLORS.get(agent_name, "#ffffff"),
                temperature=temperature,
                token_tracker=self.token_tracker,
                display_name=display_name,
            )
        finally:
            self._restore_model(original_model, did_swap)

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

        Sequential discussion model:
        - Agent 1 runs first and produces output
        - Agent 2+ each run with Agent 1's output as "colleague's analysis"
        - If 2+ agents: lead agent synthesizes all perspectives into one output
        This ensures each agent gets its own clear section in the CLI.
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

        # Sequential discussion: each agent sees the previous agents' outputs
        outputs: dict[str, str] = {}
        for i, aid in enumerate(agent_ids):
            if i == 0:
                # First agent runs without colleague context
                output = await self.run_agent(aid)
            else:
                # Subsequent agents receive all prior outputs as colleague context
                colleague_text = "\n\n---\n\n".join(
                    f"[{AGENT_REGISTRY_MAP[prev_id].name}]:\n{prev_out}"
                    for prev_id, prev_out in outputs.items()
                )
                spec = AGENT_REGISTRY_MAP.get(aid)
                colleague_names = ", ".join(
                    AGENT_REGISTRY_MAP[pid].name for pid in outputs
                )
                output = await self.run_agent(
                    aid,
                    extra_instruction=(
                        f"Your colleague(s) {colleague_names} already analyzed this. "
                        f"Review their work, add your unique perspective, and note any "
                        f"disagreements or gaps.\n\n"
                        f"Colleague analysis:\n{colleague_text}"
                    ),
                )
            # Check if agent needs user input before next agent runs
            await self.handle_user_question(output, aid)
            output = self.phase_outputs.get(aid, output)
            outputs[aid] = output

        # Synthesis: lead agent combines all perspectives into one canonical output
        synth = await self._synthesize_stage(agent_ids[0], outputs, stage_name)
        self.phase_outputs[f"STAGE_{stage_name.upper()}"] = synth
        return outputs

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

        # Model routing for debate agents
        original_model, did_swap = self._swap_model_for_agent("CHALLENGER")

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
        finally:
            self._restore_model(original_model, did_swap)

        # THINKER responds to challenges
        await self.send_status("Phase 2c: Refining analysis based on debate...", "debate")

        original_model2, did_swap2 = self._swap_model_for_agent("THINKER_REFINED")

        try:
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
            self._restore_model(original_model2, did_swap2)

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
