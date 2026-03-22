"""Skill registry — loads and serves skills to agents."""
from pathlib import Path
from agent_team.skills.types import Skill
from agent_team.skills.loader import load_skills_from_dir


class SkillRegistry:
    def __init__(self, skills_dir: Path | None = None):
        from agent_team.config import REPO_ROOT
        self.skills_dir = skills_dir or (REPO_ROOT / "skills")
        self.skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        """Load all skills from the skills directory."""
        for skill in load_skills_from_dir(self.skills_dir):
            self.skills[skill.name] = skill

    def reload(self):
        """Reload all skills from disk."""
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
        """List all registered skills with metadata."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "mode": s.mode,
                "allowed_agents": s.allowed_agents,
            }
            for s in self.skills.values()
        ]
