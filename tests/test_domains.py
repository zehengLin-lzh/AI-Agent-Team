"""Tests for domain plugins, artifact system, and domain registry."""
import pytest

from agent_team.domains.base import DomainPlugin
from agent_team.domains.coding import CodingPlugin
from agent_team.domains.writing import WritingPlugin
from agent_team.domains.research import ResearchPlugin
from agent_team.domains.data import DataPlugin
from agent_team.domains.general import GeneralPlugin
from agent_team.domains.registry import DomainRegistry, get_domain_for_task
from agent_team.artifacts.types import Artifact, ArtifactType, ArtifactStatus
from agent_team.artifacts.store import ArtifactStore
from agent_team.artifacts.renderer import render_artifact_summary


# ── Domain detection tests ─────────────────────────────────────────────────

class TestDomainDetection:
    def test_coding_detected_for_implementation(self):
        plugin = get_domain_for_task("implement a REST API endpoint with authentication")
        assert plugin.name == "coding"

    def test_writing_detected_for_reports(self):
        plugin = get_domain_for_task("write a project status report summarizing this quarter's progress")
        assert plugin.name == "writing"

    def test_research_detected_for_comparison(self):
        plugin = get_domain_for_task("research and compare alternatives to Redis for caching")
        assert plugin.name == "research"

    def test_research_detected_for_questions(self):
        plugin = get_domain_for_task("what is the latest version of Python and what are the new features?")
        assert plugin.name == "research"

    def test_data_detected_for_sql(self):
        plugin = get_domain_for_task("query the patients table and aggregate by department with count and average")
        assert plugin.name == "data"

    def test_general_fallback_for_vague(self):
        plugin = get_domain_for_task("good morning, how are you today")
        assert plugin.name == "general"

    def test_forced_domain_overrides_detection(self):
        plugin = get_domain_for_task("hello", forced_domain="coding")
        assert plugin.name == "coding"

    def test_forced_unknown_domain_falls_back(self):
        plugin = get_domain_for_task("good morning", forced_domain="nonexistent")
        # Should fall back to auto-detection — not a specific domain
        assert plugin.name in ("general", "coding")  # Either is acceptable for vague input


# ── Domain registry tests ─────────────────────────────────────────────────

class TestDomainRegistry:
    def test_lists_all_builtin_plugins(self):
        reg = DomainRegistry()
        names = reg.list_plugins()
        assert "coding" in names
        assert "writing" in names
        assert "research" in names
        assert "data" in names
        assert "general" in names

    def test_get_plugin_by_name(self):
        reg = DomainRegistry()
        assert reg.get_plugin("coding").name == "coding"
        assert reg.get_plugin("nonexistent") is None

    def test_detect_with_scores(self):
        reg = DomainRegistry()
        scores = reg.detect_with_scores("implement a Python function to sort a list")
        assert scores[0][0].name == "coding"
        assert scores[0][1] > scores[-1][1]

    def test_register_custom_plugin(self):
        reg = DomainRegistry()
        count_before = len(reg.list_plugins())

        class CustomPlugin(DomainPlugin):
            name = "custom"
            def detect(self, r): return 0.0
            def get_executor_prompt(self): return ""
            def get_reviewer_prompt(self): return ""
            def parse_output(self, r): return []

        reg.register(CustomPlugin())
        assert len(reg.list_plugins()) == count_before + 1
        assert reg.get_plugin("custom") is not None


# ── CodingPlugin parse_output tests ────────────────────────────────────────

class TestCodingPlugin:
    def test_parses_file_blocks(self):
        plugin = CodingPlugin()
        output = """Here is the code:

--- FILE: src/main.py ---
import sys

def main():
    print("hello")

if __name__ == "__main__":
    main()
--- END FILE ---

--- FILE: requirements.txt ---
fastapi>=0.100.0
--- END FILE ---
"""
        artifacts = plugin.parse_output(output)
        assert len(artifacts) == 2
        assert artifacts[0].type == ArtifactType.CODE_FILE
        assert artifacts[0].file_path == "src/main.py"
        assert artifacts[0].language == "python"
        assert "def main" in artifacts[0].content
        assert artifacts[1].file_path == "requirements.txt"

    def test_validates_empty_files(self):
        plugin = CodingPlugin()
        artifacts = [Artifact(type=ArtifactType.CODE_FILE, content="", file_path="empty.py")]
        issues = plugin.validate(artifacts)
        assert any("Empty" in i for i in issues)

    def test_detects_placeholders(self):
        plugin = CodingPlugin()
        artifacts = [Artifact(type=ArtifactType.CODE_FILE, content="def f():\n    pass  # TODO", file_path="x.py")]
        issues = plugin.validate(artifacts)
        assert any("Placeholder" in i or "TODO" in i for i in issues)


# ── WritingPlugin tests ────────────────────────────────────────────────────

class TestWritingPlugin:
    def test_parses_document_blocks(self):
        plugin = WritingPlugin()
        output = """--- DOCUMENT: Monthly Report ---
# Monthly Status Report

All projects are on track.
--- END DOCUMENT ---"""
        artifacts = plugin.parse_output(output)
        assert len(artifacts) == 1
        assert artifacts[0].type == ArtifactType.DOCUMENT
        assert artifacts[0].title == "Monthly Report"

    def test_fallback_to_full_output(self):
        plugin = WritingPlugin()
        output = "# Quick Summary\n\nEverything is fine."
        artifacts = plugin.parse_output(output)
        assert len(artifacts) == 1
        assert artifacts[0].title == "Quick Summary"


# ── DataPlugin tests ───────────────────────────────────────────────────────

class TestDataPlugin:
    def test_parses_sql_blocks(self):
        plugin = DataPlugin()
        output = """Here is the query:

```sql
SELECT p.name, COUNT(*) as visit_count
FROM patients p
JOIN appointments a ON p.id = a.patient_id
GROUP BY p.name
ORDER BY visit_count DESC;
```

This shows patient visit frequency."""
        artifacts = plugin.parse_output(output)
        # Should have both a query artifact and an analysis artifact
        types = [a.type for a in artifacts]
        assert ArtifactType.QUERY in types
        assert ArtifactType.ANALYSIS in types


# ── ArtifactStore tests ────────────────────────────────────────────────────

class TestArtifactStore:
    def test_add_and_get(self):
        store = ArtifactStore()
        a = Artifact(type=ArtifactType.CODE_FILE, content="print('hi')", file_path="hi.py")
        aid = store.add(a)
        assert store.get(aid) is a
        assert store.count == 1

    def test_by_type(self):
        store = ArtifactStore()
        store.add(Artifact(type=ArtifactType.CODE_FILE, content="x", file_path="x.py"))
        store.add(Artifact(type=ArtifactType.DOCUMENT, content="doc", title="Doc"))
        store.add(Artifact(type=ArtifactType.CODE_FILE, content="y", file_path="y.py"))
        assert len(store.by_type(ArtifactType.CODE_FILE)) == 2
        assert len(store.by_type(ArtifactType.DOCUMENT)) == 1

    def test_mark_status(self):
        store = ArtifactStore()
        a = Artifact(type=ArtifactType.CODE_FILE, content="x", file_path="x.py")
        aid = store.add(a)
        assert a.status == ArtifactStatus.DRAFT
        store.mark_validated(aid)
        assert a.status == ArtifactStatus.VALIDATED
        store.mark_written(aid)
        assert a.status == ArtifactStatus.WRITTEN

    def test_summary(self):
        store = ArtifactStore()
        store.add(Artifact(type=ArtifactType.CODE_FILE, content="x", file_path="x.py"))
        store.add(Artifact(type=ArtifactType.DOCUMENT, content="d", title="Doc"))
        summary = store.summary()
        assert summary["total"] == 2
        assert "code_file" in summary["by_type"]
        assert "document" in summary["by_type"]


# ── Artifact renderer tests ───────────────────────────────────────────────

class TestArtifactRenderer:
    def test_code_file_summary(self):
        a = Artifact(type=ArtifactType.CODE_FILE, content="a\nb\nc", file_path="main.py", language="python")
        s = render_artifact_summary(a)
        assert "python" in s
        assert "main.py" in s
        assert "3 lines" in s

    def test_document_summary(self):
        a = Artifact(type=ArtifactType.DOCUMENT, content="word " * 100, title="Report")
        s = render_artifact_summary(a)
        assert "doc" in s
        assert "Report" in s
        assert "100 words" in s

    def test_query_summary(self):
        a = Artifact(type=ArtifactType.QUERY, content="SELECT * FROM users", title="Query 1")
        s = render_artifact_summary(a)
        assert "sql" in s
        assert "SELECT" in s
