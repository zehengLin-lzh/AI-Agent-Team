"""Tests for the task graph engine, router, and enhanced classification."""
import asyncio
import pytest

from agent_team.agents.task_graph import (
    TaskGraph, TaskNode, AgentConfig, GraphExecutor, NodeStatus,
)
from agent_team.agents.router import TaskRouter
from agent_team.agents.complexity import (
    TaskComplexity, TaskClassification, classify_task, classify_complexity,
)
from agent_team.agents.definitions import AgentMode


# ── TaskGraph unit tests ───────────────────────────────────────────────────

class TestTaskGraph:
    def test_add_and_get_node(self):
        g = TaskGraph()
        node = TaskNode(id="a", config=AgentConfig(agent_id="ORCH", stage="orchestrator"))
        g.add_node(node)
        assert g.get_node("a") is node
        assert g.get_node("nonexistent") is None

    def test_ready_nodes_no_deps(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="orch")))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="orch")))
        ready = g.ready_nodes()
        assert len(ready) == 2

    def test_ready_nodes_with_deps(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="orch")))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="think"), depends_on=["a"]))
        ready = g.ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "a"

    def test_mark_complete_unlocks_dependents(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="orch")))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="think"), depends_on=["a"]))
        g.mark_complete("a", "output_a")
        ready = g.ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "b"

    def test_is_complete(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="orch")))
        assert not g.is_complete()
        g.mark_complete("a", "done")
        assert g.is_complete()

    def test_stage_output(self):
        g = TaskGraph()
        g.add_node(TaskNode(
            id="s0_plan",
            config=AgentConfig(agent_id="PLANNER", stage="planner"),
        ))
        g.mark_complete("s0_plan", "the plan")
        assert g.stage_output("planner") == "the plan"
        assert g.stage_output("nonexistent") == ""

    def test_validate_missing_dep(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="x"), depends_on=["missing"]))
        errors = g.validate()
        assert any("missing" in e for e in errors)

    def test_validate_cycle(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="x"), depends_on=["b"]))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="x"), depends_on=["a"]))
        errors = g.validate()
        assert any("Cycle" in e or "cycle" in e.lower() for e in errors)

    def test_cascade_skip_on_failure(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="x")))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="y"), depends_on=["a"]))
        g.add_node(TaskNode(id="c", config=AgentConfig(agent_id="C", stage="z"), depends_on=["b"]))
        g.mark_failed("a", "boom")
        assert g.nodes["b"].status == NodeStatus.SKIPPED
        assert g.nodes["c"].status == NodeStatus.SKIPPED
        assert g.is_complete()

    def test_optional_failure_does_not_cascade(self):
        g = TaskGraph()
        g.add_node(TaskNode(
            id="a", config=AgentConfig(agent_id="A", stage="x"), is_optional=True,
        ))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="y"), depends_on=["a"]))
        g.mark_failed("a", "optional fail")
        # b should still be ready (a is optional)
        ready = g.ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "b"

    def test_all_outputs(self):
        g = TaskGraph()
        g.add_node(TaskNode(id="a", config=AgentConfig(agent_id="A", stage="x")))
        g.add_node(TaskNode(id="b", config=AgentConfig(agent_id="B", stage="y")))
        g.mark_complete("a", "out_a")
        g.mark_complete("b", "out_b")
        assert g.all_outputs() == {"a": "out_a", "b": "out_b"}


# ── TaskRouter tests ──────────────────────────────────────────────────────

class TestTaskRouter:
    def _make_classification(self, complexity: str = "medium", domain: str = "coding"):
        return TaskClassification(
            complexity=TaskComplexity(complexity),
            domain=domain,
        )

    def test_simple_coding_graph_structure(self):
        router = TaskRouter()
        graph = router.route(
            self._make_classification("simple"),
            mode=AgentMode.CODING,
        )
        # SIMPLE coding: ORCHESTRATOR → PLANNER → EXECUTOR → REVIEWER = 4 nodes
        assert len(graph.nodes) == 4
        errors = graph.validate()
        assert errors == []

    def test_simple_thinking_graph_structure(self):
        router = TaskRouter()
        graph = router.route(
            self._make_classification("simple"),
            mode=AgentMode.THINKING,
        )
        # SIMPLE thinking: ORCHESTRATOR → PLANNER → REVIEWER = 3 nodes
        assert len(graph.nodes) == 3
        errors = graph.validate()
        assert errors == []

    def test_medium_coding_graph_has_multi_agent_stages(self):
        router = TaskRouter()
        graph = router.route(
            self._make_classification("medium"),
            mode=AgentMode.CODING,
        )
        # MEDIUM coding: [ORCH_LUMUSI, ORCH_IVOR] → [THINK_SOREN] → [PLAN_ATLAS] → [EXEC_KAI] → [REV_QUINN, REV_LENA]
        # Multi-agent stages have think+discuss+synthesis nodes
        assert len(graph.nodes) > 5  # More nodes due to multi-agent stages
        errors = graph.validate()
        assert errors == []

    def test_plan_only_skips_executor_reviewer(self):
        router = TaskRouter()
        graph = router.route(
            self._make_classification("simple"),
            mode=AgentMode.CODING,
            plan_only=True,
        )
        # plan_only: ORCHESTRATOR → PLANNER (no EXECUTOR, no REVIEWER)
        stage_names = {n.config.stage for n in graph.nodes.values()}
        assert "executor" not in stage_names
        assert "reviewer" not in stage_names

    def test_graph_is_valid_dag(self):
        """All generated graphs must be valid DAGs with no cycles or missing deps."""
        router = TaskRouter()
        for complexity in ["simple", "medium", "complex"]:
            for mode in [AgentMode.CODING, AgentMode.THINKING, AgentMode.ARCHITECTURE]:
                graph = router.route(
                    self._make_classification(complexity),
                    mode=mode,
                )
                errors = graph.validate()
                assert errors == [], f"Invalid graph for {complexity}/{mode}: {errors}"

    def test_all_nodes_have_valid_agent_ids(self):
        """Every node must reference a known agent ID."""
        from agent_team.agents.definitions import AGENT_REGISTRY_MAP
        _ALL_KNOWN = set(AGENT_REGISTRY_MAP.keys()) | {
            "ORCHESTRATOR", "THINKER", "PLANNER", "EXECUTOR", "REVIEWER",
        }
        router = TaskRouter()
        for complexity in ["simple", "medium", "complex"]:
            graph = router.route(
                self._make_classification(complexity),
                mode=AgentMode.CODING,
            )
            for node in graph.nodes.values():
                assert node.config.agent_id in _ALL_KNOWN, (
                    f"Unknown agent '{node.config.agent_id}' in {complexity} graph"
                )


# ── Enhanced classification tests ─────────────────────────────────────────

class TestClassifyTask:
    def test_backward_compatible(self):
        """classify_task().complexity matches classify_complexity() for same input."""
        for text in [
            "fix typo in README",
            "refactor the authentication system to use OAuth2",
            "add a hello world endpoint",
        ]:
            old = classify_complexity(text, "coding")
            new = classify_task(text, "coding")
            assert new.complexity == old, f"Mismatch for '{text}': {old} vs {new.complexity}"

    def test_coding_domain_detected(self):
        result = classify_task("implement a REST API endpoint for user authentication", "coding")
        assert result.domain == "coding"

    def test_writing_domain_detected(self):
        result = classify_task("write a project status report summarizing this quarter", "thinking")
        assert result.domain == "writing"

    def test_research_domain_detected(self):
        result = classify_task("research alternatives to Redis for caching and compare pros and cons", "thinking")
        assert result.domain == "research"

    def test_data_domain_detected(self):
        result = classify_task("query the table to show all patients with overdue appointments grouped by count", "thinking")
        assert result.domain == "data"

    def test_general_fallback(self):
        result = classify_task("hello", "thinking")
        assert result.domain == "general"

    def test_needs_tools_detected(self):
        result = classify_task("search the web for latest Python 3.13 features", "thinking")
        assert result.needs_tools is True

    def test_no_tools_for_simple(self):
        result = classify_task("explain what a monad is", "thinking")
        assert result.needs_tools is False

    def test_returns_dataclass(self):
        result = classify_task("build a dashboard", "coding")
        assert isinstance(result, TaskClassification)
        assert isinstance(result.complexity, TaskComplexity)
        assert isinstance(result.domain, str)
        assert isinstance(result.key_entities, list)
