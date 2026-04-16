"""Task graph engine — DAG-based agent execution with dependency management.

Replaces the fixed phase-order pipeline with a dynamic directed acyclic graph (DAG)
where each node is an agent invocation with explicit dependencies. Independent nodes
execute in parallel via asyncio.gather().
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.events import EventEmitter


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AgentConfig:
    """Configuration for a single agent invocation."""
    agent_id: str           # e.g. "ORCHESTRATOR", "ORCH_LUMUSI", "THINK_SOREN"
    stage: str              # orchestrator, thinker, planner, executor, reviewer
    model_tier: str = "fast"  # fast, reasoning, coding
    temperature: float = 0.3
    extra_instruction: str = ""
    is_synthesis: bool = False  # If True, synthesizes peer outputs


@dataclass
class TaskNode:
    """A single node in the task graph — one agent invocation."""
    id: str
    config: AgentConfig
    depends_on: list[str] = field(default_factory=list)
    # Peers in the same stage — used for discussion/synthesis
    peers: list[str] = field(default_factory=list)
    # Runtime state
    status: NodeStatus = NodeStatus.PENDING
    output: str = ""
    error: str = ""
    # Flags
    is_optional: bool = False  # If True, failure doesn't block dependents
    skip_if_plan_only: bool = False  # Executor/reviewer nodes in plan-only mode


@dataclass
class TaskGraph:
    """DAG of TaskNodes with dependency tracking.

    Usage:
        graph = TaskGraph()
        graph.add_node(TaskNode(id="orch", config=..., depends_on=[]))
        graph.add_node(TaskNode(id="think", config=..., depends_on=["orch"]))
        while not graph.is_complete():
            for node in graph.ready_nodes():
                # execute node
                graph.mark_complete(node.id, output)
    """
    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def add_node(self, node: TaskNode) -> None:
        self.nodes[node.id] = node

    def get_node(self, node_id: str) -> TaskNode | None:
        return self.nodes.get(node_id)

    def ready_nodes(self) -> list[TaskNode]:
        """Return nodes whose dependencies are all satisfied (completed/skipped)."""
        ready = []
        done_statuses = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.FAILED}
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            deps_satisfied = all(
                self.nodes[dep].status in done_statuses
                for dep in node.depends_on
                if dep in self.nodes
            )
            if deps_satisfied:
                ready.append(node)
        return ready

    def mark_complete(self, node_id: str, output: str) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.COMPLETED
        node.output = output

    def mark_failed(self, node_id: str, error: str) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.FAILED
        node.error = error
        # Skip dependents of non-optional failed nodes
        if not node.is_optional:
            self._cascade_skip(node_id)

    def mark_skipped(self, node_id: str) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.SKIPPED

    def is_complete(self) -> bool:
        """True when no nodes are PENDING or RUNNING."""
        return all(
            n.status not in (NodeStatus.PENDING, NodeStatus.RUNNING)
            for n in self.nodes.values()
        )

    def stage_output(self, stage: str) -> str:
        """Get the synthesized output for a stage, or the last completed node's output."""
        # Prefer synthesis node
        for node in self.nodes.values():
            if node.config.stage == stage and node.config.is_synthesis and node.status == NodeStatus.COMPLETED:
                return node.output
        # Fallback: last completed node in stage
        for node in reversed(list(self.nodes.values())):
            if node.config.stage == stage and node.status == NodeStatus.COMPLETED:
                return node.output
        return ""

    def all_outputs(self) -> dict[str, str]:
        """Return all completed node outputs keyed by node ID."""
        return {
            n.id: n.output
            for n in self.nodes.values()
            if n.status == NodeStatus.COMPLETED and n.output
        }

    def validate(self) -> list[str]:
        """Validate the graph. Returns list of errors (empty = valid)."""
        errors = []
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    errors.append(f"Node '{node.id}' depends on unknown node '{dep}'")
        # Check for cycles via topological sort
        if not errors:
            visited = set()
            path = set()

            def _has_cycle(nid: str) -> bool:
                if nid in path:
                    return True
                if nid in visited:
                    return False
                path.add(nid)
                visited.add(nid)
                for dep in self.nodes.get(nid, TaskNode(id="", config=AgentConfig(agent_id="", stage=""))).depends_on:
                    if _has_cycle(dep):
                        return True
                path.discard(nid)
                return False

            for nid in self.nodes:
                if _has_cycle(nid):
                    errors.append(f"Cycle detected involving node '{nid}'")
                    break
        return errors

    def _cascade_skip(self, failed_id: str) -> None:
        """Skip all nodes that transitively depend on a failed node."""
        for node in self.nodes.values():
            if node.status == NodeStatus.PENDING and failed_id in node.depends_on:
                node.status = NodeStatus.SKIPPED
                self._cascade_skip(node.id)

    def summary(self) -> str:
        """Human-readable summary of graph execution."""
        lines = []
        for node in self.nodes.values():
            deps = ", ".join(node.depends_on) if node.depends_on else "none"
            lines.append(f"  {node.id} [{node.config.stage}] → {node.status.value} (deps: {deps})")
        return "\n".join(lines)


class GraphExecutor:
    """Executes a TaskGraph respecting dependencies, parallelizing independent nodes.

    Delegates actual agent execution to the AgentTeam instance — this class
    only manages scheduling and dependency resolution.
    """

    def __init__(self, team, graph: TaskGraph):
        """
        Args:
            team: AgentTeam instance (provides run_agent, run_stage, etc.)
            graph: The task graph to execute.
        """
        self.team = team
        self.graph = graph

    async def execute(self) -> dict[str, str]:
        """Run all nodes in the graph, returning outputs keyed by node ID."""
        errors = self.graph.validate()
        if errors:
            raise ValueError(f"Invalid task graph: {'; '.join(errors)}")

        while not self.graph.is_complete():
            ready = self.graph.ready_nodes()
            if not ready:
                # Deadlock — no nodes are ready but graph isn't complete
                pending = [n.id for n in self.graph.nodes.values() if n.status == NodeStatus.PENDING]
                raise RuntimeError(f"Task graph deadlock. Pending nodes: {pending}")

            if len(ready) == 1:
                await self._execute_node(ready[0])
            else:
                # Parallel execution of independent nodes
                await asyncio.gather(
                    *[self._execute_node(node) for node in ready],
                )

        return self.graph.all_outputs()

    async def _execute_node(self, node: TaskNode) -> None:
        """Execute a single graph node via the AgentTeam's run_agent method."""
        node.status = NodeStatus.RUNNING

        try:
            if node.config.is_synthesis:
                output = await self._run_synthesis_node(node)
            else:
                output = await self.team.run_agent(
                    node.config.agent_id,
                    extra_instruction=node.config.extra_instruction,
                )
                # Handle user questions
                await self.team.handle_user_question(output, node.config.agent_id)
                output = self.team.phase_outputs.get(node.config.agent_id, output)

            self.graph.mark_complete(node.id, output)

            # Store in phase_outputs for backward compatibility
            stage_key = f"STAGE_{node.config.stage.upper()}"
            self.team.phase_outputs[stage_key] = output
            # Legacy key compat
            _LEGACY_MAP = {
                "orchestrator": "ORCHESTRATOR",
                "thinker": "THINKER",
                "planner": "PLANNER",
                "executor": "EXECUTOR",
                "reviewer": "REVIEWER",
            }
            if node.config.stage in _LEGACY_MAP:
                self.team.phase_outputs[_LEGACY_MAP[node.config.stage]] = output

        except Exception as e:
            self.graph.mark_failed(node.id, str(e))
            await self.team.send_status(
                f"Node {node.id} failed: {str(e)[:100]}", "error",
            )

    async def _run_synthesis_node(self, node: TaskNode) -> str:
        """Synthesize outputs from peer nodes."""
        from agent_team.agents.definitions import SYNTHESIS_PROMPT, AGENT_REGISTRY_MAP

        peer_outputs = {}
        for peer_id in node.peers:
            peer_node = self.graph.get_node(peer_id)
            if peer_node and peer_node.status == NodeStatus.COMPLETED:
                spec = AGENT_REGISTRY_MAP.get(peer_node.config.agent_id)
                label = spec.name if spec else peer_node.config.agent_id
                peer_outputs[label] = peer_node.output

        if not peer_outputs:
            return ""

        perspectives = "\n\n---\n\n".join(
            f"[{name}]:\n{out}" for name, out in peer_outputs.items()
        )

        output = await self.team.run_agent(
            node.config.agent_id,
            extra_instruction=f"{SYNTHESIS_PROMPT}\n\n{perspectives}",
        )
        return output
