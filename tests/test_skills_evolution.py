"""Tests for C1 Skill Self-Evolution: writer, promoter, registry review flow."""
from __future__ import annotations

import pytest

from agent_team.memory.types import LearnedPattern
from agent_team.skills.loader import load_skill, load_skills_from_dir
from agent_team.skills.promoter import (
    _parse_promotion,
    _agents_for_category,
    promote_pattern_to_skill,
)
from agent_team.skills.registry import PENDING_SUBDIR, SkillRegistry
from agent_team.skills.types import Skill
from agent_team.skills.writer import (
    delete_skill,
    move_skill,
    skill_to_markdown,
    slugify,
    write_skill,
)


# ── writer ────────────────────────────────────────────────────────────────

class TestWriter:
    def test_slugify_basic(self):
        assert slugify("Hello World") == "hello-world"
        assert slugify("Use_case 42!") == "use_case-42"
        assert slugify("   ") == "skill"

    def test_roundtrip_write_then_load(self, tmp_path):
        skill = Skill(
            name="avoid none eq",
            description="Use `is None` instead of `== None`",
            mode="coding",
            instructions="When comparing to None, always use `is None` or `is not None`.",
            allowed_agents=["EXECUTOR", "EXEC_KAI"],
        )
        path = write_skill(skill, tmp_path)
        assert path.exists()
        reloaded = load_skill(path)
        assert reloaded is not None
        assert reloaded.name == skill.name
        assert reloaded.description == skill.description
        assert reloaded.mode == skill.mode
        assert reloaded.instructions == skill.instructions
        assert reloaded.allowed_agents == skill.allowed_agents

    def test_write_to_pending_subdirectory(self, tmp_path):
        skill = Skill(
            name="skillA", description="d", mode="all", instructions="body",
        )
        path = write_skill(skill, tmp_path, subdirectory="pending")
        assert "pending" in path.parts
        assert path.read_text().startswith("---")

    def test_skill_to_markdown_structure(self):
        skill = Skill(name="test", description="a desc", mode="all", instructions="body")
        md = skill_to_markdown(skill)
        assert md.startswith("---\n")
        assert "name: test" in md
        assert "mode: all" in md
        assert "body" in md

    def test_delete_skill(self, tmp_path):
        skill = Skill(name="to-del", description="d", mode="all", instructions="b")
        path = write_skill(skill, tmp_path)
        assert path.exists()
        assert delete_skill("to-del", tmp_path) is True
        assert delete_skill("does-not-exist", tmp_path) is False
        assert not path.parent.exists()

    def test_move_skill_pending_to_root(self, tmp_path):
        skill = Skill(name="mover", description="d", mode="all", instructions="b")
        write_skill(skill, tmp_path, subdirectory="pending")
        new_path = move_skill(
            "mover", tmp_path, from_subdirectory="pending", to_subdirectory=""
        )
        assert new_path is not None
        assert new_path.exists()
        assert "pending" not in new_path.parts

    def test_move_missing_returns_none(self, tmp_path):
        assert move_skill("nope", tmp_path, from_subdirectory="pending") is None


# ── loader: exclude_subdirs ─────────────────────────────────────────────

class TestLoaderExclusions:
    def test_exclude_pending(self, tmp_path):
        approved = Skill(name="approved", description="d", mode="all", instructions="b1")
        candidate = Skill(name="candidate", description="d", mode="all", instructions="b2")
        write_skill(approved, tmp_path)
        write_skill(candidate, tmp_path, subdirectory="pending")

        all_skills = load_skills_from_dir(tmp_path)
        approved_only = load_skills_from_dir(tmp_path, exclude_subdirs=["pending"])

        assert {s.name for s in all_skills} == {"approved", "candidate"}
        assert {s.name for s in approved_only} == {"approved"}


# ── registry ─────────────────────────────────────────────────────────────

class TestRegistry:
    def test_registry_skips_pending_by_default(self, tmp_path):
        write_skill(
            Skill(name="live", description="d", mode="all", instructions="b"),
            tmp_path,
        )
        write_skill(
            Skill(name="draft", description="d", mode="all", instructions="b"),
            tmp_path,
            subdirectory=PENDING_SUBDIR,
        )
        registry = SkillRegistry(skills_dir=tmp_path)
        names = [s["name"] for s in registry.list_skills()]
        assert "live" in names
        assert "draft" not in names

    def test_list_pending_separately(self, tmp_path):
        write_skill(
            Skill(name="draft", description="d", mode="all", instructions="b"),
            tmp_path,
            subdirectory=PENDING_SUBDIR,
        )
        registry = SkillRegistry(skills_dir=tmp_path)
        pending = registry.list_pending()
        assert len(pending) == 1
        assert pending[0].name == "draft"

    def test_approve_moves_to_root(self, tmp_path):
        registry = SkillRegistry(skills_dir=tmp_path)
        registry.stage_candidate(
            Skill(name="approveMe", description="d", mode="all", instructions="b")
        )
        assert len(registry.list_pending()) == 1
        new_path = registry.approve_pending("approveMe")
        assert new_path is not None
        assert len(registry.list_pending()) == 0
        assert "approveMe" in {s["name"] for s in registry.list_skills()}

    def test_reject_deletes_pending(self, tmp_path):
        registry = SkillRegistry(skills_dir=tmp_path)
        registry.stage_candidate(
            Skill(name="rejectMe", description="d", mode="all", instructions="b")
        )
        assert registry.reject_pending("rejectMe") is True
        assert registry.list_pending() == []
        assert registry.reject_pending("missing") is False

    def test_candidate_exists_matches_either_state(self, tmp_path):
        registry = SkillRegistry(skills_dir=tmp_path)
        registry.stage_candidate(
            Skill(name="foo", description="d", mode="all", instructions="b")
        )
        assert registry.candidate_exists("foo") is True
        registry.approve_pending("foo")
        assert registry.candidate_exists("foo") is True
        assert registry.candidate_exists("unknown") is False


# ── promoter ─────────────────────────────────────────────────────────────

class TestPromoterParsing:
    def test_parse_full_output(self):
        raw = (
            "NAME: use is-none\n"
            "DESCRIPTION: always use `is None` for null checks\n"
            "INSTRUCTIONS:\n"
            "Use `is None`; never `== None`.\n"
        )
        parsed = _parse_promotion(raw)
        assert parsed is not None
        name, desc, instr = parsed
        assert name == "use is-none"
        assert "is None" in instr

    def test_parse_skip(self):
        assert _parse_promotion("SKIP: too vague to be reusable") is None

    def test_parse_malformed(self):
        assert _parse_promotion("random text without format") is None

    def test_agents_for_known_category(self):
        agents = _agents_for_category("import_error")
        assert "EXECUTOR" in agents

    def test_agents_for_unknown_category(self):
        agents = _agents_for_category("something_weird")
        assert "EXECUTOR" in agents and "REVIEWER" in agents


class TestPromotePattern:
    @pytest.mark.asyncio
    async def test_promote_returns_skill(self):
        pattern = LearnedPattern(
            id="p1",
            category="import_error",
            description="mistake: forgot json import | fix: added import json | prevention: verify imports",
            confidence=0.85,
        )
        async def fake_llm(*, system_prompt, messages, temperature):
            return (
                "NAME: verify imports\n"
                "DESCRIPTION: make sure every used module is imported\n"
                "INSTRUCTIONS:\n"
                "Before finishing a file, scan every identifier and confirm there is a matching import.\n"
            )
        skill = await promote_pattern_to_skill(pattern, llm_caller=fake_llm)
        assert skill is not None
        assert skill.name == "verify imports"
        assert "EXECUTOR" in skill.allowed_agents
        assert skill.requires["source_pattern_id"] == "p1"

    @pytest.mark.asyncio
    async def test_promote_returns_none_on_skip(self):
        pattern = LearnedPattern(id="p2", category="error_fix", description="x", confidence=0.9)
        async def fake_llm(*, system_prompt, messages, temperature):
            return "SKIP: pattern too vague"
        assert await promote_pattern_to_skill(pattern, llm_caller=fake_llm) is None

    @pytest.mark.asyncio
    async def test_promote_returns_none_on_llm_failure(self):
        pattern = LearnedPattern(id="p3", category="error_fix", description="x", confidence=0.9)
        async def fake_llm(*, system_prompt, messages, temperature):
            raise RuntimeError("llm down")
        assert await promote_pattern_to_skill(pattern, llm_caller=fake_llm) is None


# ── extractor hook (skipping if LLM/DB path too heavy) ──────────────────

class TestExtractorStaging:
    @pytest.mark.asyncio
    async def test_maybe_stage_writes_pending(self, tmp_path, monkeypatch):
        """High-confidence pattern → candidate appears in skills/pending/."""
        from agent_team.learning import extractor as extractor_mod

        monkeypatch.setattr(
            "agent_team.config.REPO_ROOT", tmp_path, raising=False
        )

        async def fake_promote(pattern, *, llm_caller=None):
            return Skill(
                name=f"skill-{pattern.id}",
                description="generated",
                mode="all",
                instructions="body",
                allowed_agents=["EXECUTOR"],
                requires={"source_pattern_id": pattern.id},
            )

        monkeypatch.setattr(
            "agent_team.skills.promoter.promote_pattern_to_skill",
            fake_promote,
        )

        patterns = [
            LearnedPattern(id="hp", category="error_fix", description="d", confidence=0.9),
            LearnedPattern(id="lp", category="error_fix", description="d", confidence=0.3),
        ]

        count = await extractor_mod._maybe_stage_candidates(patterns)
        assert count == 1

        skills_dir = tmp_path / "skills" / "pending"
        assert skills_dir.exists()
        candidates = list(skills_dir.rglob("SKILL.md"))
        assert len(candidates) == 1
