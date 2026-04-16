"""Task router — builds a TaskGraph from task classification.

Two modes:
- Static (default): produces graphs identical to current SIMPLE/MEDIUM/COMPLEX phase orders
- Dynamic (config flag): LLM-assisted routing for optimal agent selection

The static mode guarantees backward compatibility — same agents, same order,
same output. The dynamic mode enables domain-aware routing (e.g., skip EXECUTOR
for pure research tasks, use fewer agents for simple questions).
"""
from __future__ import annotations

from agent_team.agents.task_graph import TaskGraph, TaskNode, AgentConfig
from agent_team.agents.complexity import TaskClassification, TaskComplexity
from agent_team.agents.definitions import (
    AgentMode, AGENT_REGISTRY_MAP, MODE_TEMPERATURES,
    SIMPLE_PHASE_ORDER, MEDIUM_PHASE_ORDER, COMPLEX_PHASE_ORDER,
)
from agent_team.config import (
    MODEL_ROUTING, SIMPLE_MODEL_ROUTING, MEDIUM_MODEL_ROUTING,
)


def _tier_for_agent(agent_id: str, complexity: TaskComplexity) -> str:
    """Resolve the model tier name for an agent at a given complexity."""
    if complexity == TaskComplexity.SIMPLE:
        model = SIMPLE_MODEL_ROUTING.get(agent_id) or MODEL_ROUTING.get(agent_id)
    elif complexity == TaskComplexity.MEDIUM:
        model = MEDIUM_MODEL_ROUTING.get(agent_id) or MODEL_ROUTING.get(agent_id)
    else:
        model = MODEL_ROUTING.get(agent_id)
    # Infer tier from the model routing table
    spec = AGENT_REGISTRY_MAP.get(agent_id)
    return spec.model_tier if spec else "fast"


def _stage_for_agent(agent_id: str) -> str:
    """Get the stage name for an agent ID."""
    spec = AGENT_REGISTRY_MAP.get(agent_id)
    if spec:
        return spec.stage
    # Legacy agents
    for stage in ("orchestrator", "thinker", "planner", "executor", "reviewer"):
        if stage.upper() in agent_id.upper():
            return stage
    return "unknown"


class TaskRouter:
    """Builds a TaskGraph from task classification and mode."""

    def route(
        self,
        classification: TaskClassification,
        mode: AgentMode = AgentMode.CODING,
        plan_only: bool = False,
        reuse_plan: bool = False,
        domain_plugin=None,
    ) -> TaskGraph:
        """Build a task graph using static routing (mirrors current phase orders).

        Returns a TaskGraph that is structurally identical to the current
        SIMPLE/MEDIUM/COMPLEX phase orders, ensuring zero behavior change.
        """
        return self._build_static_graph(
            classification, mode, plan_only, reuse_plan, domain_plugin,
        )

    def _build_static_graph(
        self,
        classification: TaskClassification,
        mode: AgentMode,
        plan_only: bool,
        reuse_plan: bool,
        domain_plugin=None,
    ) -> TaskGraph:
        """Convert static phase orders into a TaskGraph."""
        complexity = classification.complexity
        temperature = MODE_TEMPERATURES.get(mode, 0.3)

        # Select the phase order (same logic as current runner.py)
        if complexity == TaskComplexity.SIMPLE:
            phase_order = list(SIMPLE_PHASE_ORDER.get(mode, SIMPLE_PHASE_ORDER[AgentMode.CODING]))
        elif complexity == TaskComplexity.MEDIUM:
            phase_order = list(MEDIUM_PHASE_ORDER.get(mode, MEDIUM_PHASE_ORDER[AgentMode.CODING]))
        else:
            phase_order = list(COMPLEX_PHASE_ORDER.get(mode, COMPLEX_PHASE_ORDER[AgentMode.CODING]))

        # Filter for plan_only or reuse_plan
        _exec_rev_ids = {"EXECUTOR", "EXEC_KAI", "EXEC_DEV", "EXEC_SAGE",
                         "REVIEWER", "REV_QUINN", "REV_LENA"}
        if reuse_plan:
            phase_order = [g for g in phase_order if _exec_rev_ids.intersection(g)]
            if not phase_order:
                phase_order = [["EXECUTOR"], ["REVIEWER"]]
        elif plan_only:
            phase_order = [g for g in phase_order if not _exec_rev_ids.intersection(g)]

        graph = TaskGraph()
        prev_stage_nodes: list[str] = []

        for stage_idx, phase_group in enumerate(phase_order):
            stage_name = _stage_for_agent(phase_group[0])
            is_named = any(a in AGENT_REGISTRY_MAP for a in phase_group)

            if is_named and len(phase_group) > 1:
                # Multi-agent stage: think → discuss → synthesis
                stage_node_ids = self._add_multi_agent_stage(
                    graph, phase_group, stage_name, stage_idx,
                    complexity, temperature, prev_stage_nodes,
                )
            else:
                # Single agent (or legacy single-agent)
                agent_id = phase_group[0]
                node_id = f"s{stage_idx}_{agent_id}"
                skip = (not reuse_plan and plan_only and
                        agent_id in _exec_rev_ids)
                # Inject domain-specific prompts for executor/reviewer
                extra = ""
                if domain_plugin and stage_name == "executor":
                    extra = domain_plugin.get_executor_prompt()
                elif domain_plugin and stage_name == "reviewer":
                    extra = domain_plugin.get_reviewer_prompt()
                graph.add_node(TaskNode(
                    id=node_id,
                    config=AgentConfig(
                        agent_id=agent_id,
                        stage=stage_name,
                        model_tier=_tier_for_agent(agent_id, complexity),
                        temperature=temperature,
                        extra_instruction=extra,
                    ),
                    depends_on=list(prev_stage_nodes),
                    skip_if_plan_only=skip,
                ))
                stage_node_ids = [node_id]

            prev_stage_nodes = stage_node_ids

        return graph

    def _add_multi_agent_stage(
        self,
        graph: TaskGraph,
        agent_ids: list[str],
        stage_name: str,
        stage_idx: int,
        complexity: TaskComplexity,
        temperature: float,
        prev_stage_nodes: list[str],
    ) -> list[str]:
        """Add nodes for a multi-agent stage (think → discuss → synthesis).

        Returns the synthesis node ID list (single element) as the stage output.
        """
        # Phase 1: Parallel independent thinking
        think_node_ids = []
        for agent_id in agent_ids:
            node_id = f"s{stage_idx}_{agent_id}_think"
            graph.add_node(TaskNode(
                id=node_id,
                config=AgentConfig(
                    agent_id=agent_id,
                    stage=stage_name,
                    model_tier=_tier_for_agent(agent_id, complexity),
                    temperature=temperature,
                ),
                depends_on=list(prev_stage_nodes),
            ))
            think_node_ids.append(node_id)

        # Phase 2: Parallel discussion (each agent sees all think outputs)
        discuss_node_ids = []
        for agent_id in agent_ids:
            node_id = f"s{stage_idx}_{agent_id}_discuss"
            graph.add_node(TaskNode(
                id=node_id,
                config=AgentConfig(
                    agent_id=agent_id,
                    stage=stage_name,
                    model_tier=_tier_for_agent(agent_id, complexity),
                    temperature=temperature,
                    extra_instruction=(
                        "Your colleagues have completed their independent analysis. "
                        "Review all perspectives below. Be concise — only address "
                        "disagreements, gaps, and strengthen the strongest ideas."
                    ),
                ),
                depends_on=think_node_ids,  # Wait for all think nodes
                peers=think_node_ids,
            ))
            discuss_node_ids.append(node_id)

        # Phase 3: Synthesis (lead agent combines all discussion outputs)
        synth_id = f"s{stage_idx}_{stage_name}_synthesis"
        graph.add_node(TaskNode(
            id=synth_id,
            config=AgentConfig(
                agent_id=agent_ids[0],  # Lead agent does synthesis
                stage=stage_name,
                model_tier=_tier_for_agent(agent_ids[0], complexity),
                temperature=temperature,
                is_synthesis=True,
            ),
            depends_on=discuss_node_ids,
            peers=discuss_node_ids,
        ))

        return [synth_id]
