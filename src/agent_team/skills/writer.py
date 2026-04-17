"""Serialize Skill objects back to SKILL.md files (reverse of loader)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from agent_team.skills.types import Skill


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def slugify(name: str) -> str:
    """Convert a skill name into a safe directory name."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "skill"


def _escape_yaml_value(value: str) -> str:
    """Quote a scalar if it contains YAML-special characters."""
    if re.search(r'[:#\[\]{}&*!|>"\'`%@,?\-]', value) or value != value.strip():
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _format_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def skill_to_markdown(skill: Skill) -> str:
    """Serialize a Skill to frontmatter + body format (parsed by loader)."""
    lines = ["---"]
    lines.append(f"name: {_escape_yaml_value(skill.name)}")
    lines.append(f"description: {_escape_yaml_value(skill.description)}")
    lines.append(f"mode: {_escape_yaml_value(skill.mode)}")
    if skill.allowed_agents:
        lines.append(f"allowed_agents: {_format_list(skill.allowed_agents)}")
    lines.append("---")
    lines.append("")
    lines.append(skill.instructions.strip())
    lines.append("")
    return "\n".join(lines)


def write_skill(
    skill: Skill,
    skills_dir: Path,
    *,
    subdirectory: str = "",
    overwrite: bool = True,
) -> Path:
    """Write a Skill to ``skills_dir[/subdirectory]/<slug>/SKILL.md``.

    subdirectory="pending" places the candidate in the review queue.
    Returns the path of the written file.
    """
    base = skills_dir
    if subdirectory:
        base = base / subdirectory
    skill_dir = base / slugify(skill.name)
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists() and not overwrite:
        raise FileExistsError(f"Skill already exists: {skill_file}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(skill_to_markdown(skill), encoding="utf-8")
    return skill_file


def delete_skill(name: str, skills_dir: Path, *, subdirectory: str = "") -> bool:
    """Remove a skill directory. Returns True if something was deleted."""
    base = skills_dir / subdirectory if subdirectory else skills_dir
    skill_dir = base / slugify(name)
    if not skill_dir.exists():
        return False
    shutil.rmtree(skill_dir)
    return True


def move_skill(
    name: str,
    skills_dir: Path,
    *,
    from_subdirectory: str,
    to_subdirectory: str = "",
) -> Path | None:
    """Move a skill directory between subdirectories (e.g. pending -> root).

    Returns the new SKILL.md path on success, None if the source was missing.
    """
    src = skills_dir / from_subdirectory / slugify(name)
    if not src.exists():
        return None
    dst_parent = skills_dir / to_subdirectory if to_subdirectory else skills_dir
    dst = dst_parent / slugify(name)
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst / "SKILL.md"
