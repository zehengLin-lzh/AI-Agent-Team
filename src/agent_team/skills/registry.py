"""Skill registry — loads and serves skills to agents."""
from pathlib import Path

from agent_team.skills.loader import load_skills_from_dir
from agent_team.skills.types import Skill
from agent_team.skills.writer import (
    delete_skill,
    move_skill,
    slugify,
    write_skill,
)

PENDING_SUBDIR = "pending"


class SkillRegistry:
    def __init__(self, skills_dir: Path | None = None):
        from agent_team.config import REPO_ROOT
        self.skills_dir = skills_dir or (REPO_ROOT / "skills")
        self.skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        """Load approved skills (skipping the pending/ subdirectory)."""
        if not self.skills_dir.exists():
            return
        for skill in load_skills_from_dir(self.skills_dir, exclude_subdirs=[PENDING_SUBDIR]):
            self.skills[skill.name] = skill

    def reload(self):
        """Reload approved skills from disk."""
        self.skills.clear()
        self._load_all()

    def get_skills_for_agent(self, agent_name: str, mode: str) -> list[Skill]:
        """Return skills applicable to this agent in this mode."""
        results = []
        for skill in self.skills.values():
            if skill.mode not in ("all", mode):
                continue
            if agent_name not in skill.allowed_agents:
                continue
            results.append(skill)
        return results

    def format_skills_prompt(self, agent_name: str, mode: str) -> str:
        """Format relevant skills as additional system prompt text."""
        skills = self.get_skills_for_agent(agent_name, mode)
        if not skills:
            return ""

        parts = ["## Available Skills & Knowledge\n"]
        for skill in skills:
            parts.append(f"### {skill.name}: {skill.description}\n")
            parts.append(skill.instructions)
            parts.append("")

        return "\n".join(parts)

    def list_skills(self) -> list[dict]:
        """List all registered (approved) skills with metadata."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "mode": s.mode,
                "allowed_agents": s.allowed_agents,
            }
            for s in self.skills.values()
        ]

    # ── Candidate / pending skill management ───────────────────────────

    def _pending_dir(self) -> Path:
        return self.skills_dir / PENDING_SUBDIR

    def list_pending(self) -> list[Skill]:
        """Load and return all candidate skills awaiting review."""
        pending_dir = self._pending_dir()
        if not pending_dir.exists():
            return []
        return load_skills_from_dir(pending_dir)

    def get_pending(self, name: str) -> Skill | None:
        for skill in self.list_pending():
            if skill.name == name:
                return skill
        return None

    def stage_candidate(self, skill: Skill) -> Path:
        """Write a skill into the pending/ queue. Returns the SKILL.md path."""
        return write_skill(skill, self.skills_dir, subdirectory=PENDING_SUBDIR)

    def approve_pending(self, name: str) -> Path | None:
        """Promote a pending skill into the live registry and reload."""
        result = move_skill(
            name,
            self.skills_dir,
            from_subdirectory=PENDING_SUBDIR,
            to_subdirectory="",
        )
        if result is not None:
            self.reload()
        return result

    def reject_pending(self, name: str) -> bool:
        """Delete a pending candidate without approving it."""
        return delete_skill(name, self.skills_dir, subdirectory=PENDING_SUBDIR)

    def delete_approved(self, name: str) -> bool:
        """Delete an approved skill and reload."""
        deleted = delete_skill(name, self.skills_dir)
        if deleted:
            self.reload()
        return deleted

    def candidate_exists(self, name: str) -> bool:
        """True if a pending or approved skill with this name already exists."""
        if name in self.skills:
            return True
        slug = slugify(name)
        return (self._pending_dir() / slug / "SKILL.md").exists()
