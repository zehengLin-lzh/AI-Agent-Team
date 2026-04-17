"""Load skills from markdown files with YAML frontmatter."""
import re
from pathlib import Path
from agent_team.skills.types import Skill


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.
    Returns (metadata_dict, body_text)."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
    if not match:
        return {}, content

    frontmatter_text = match.group(1)
    body = match.group(2)

    # Simple YAML parser (avoids pyyaml dependency for basic cases)
    metadata = {}
    for line in frontmatter_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            # Handle lists in bracket notation: [a, b, c]
            if value.startswith('[') and value.endswith(']'):
                items = [item.strip().strip('"').strip("'") for item in value[1:-1].split(',')]
                metadata[key] = [i for i in items if i]
            elif value.startswith('"') and value.endswith('"'):
                metadata[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                metadata[key] = value[1:-1]
            else:
                metadata[key] = value

    return metadata, body


def load_skill(skill_path: Path) -> Skill | None:
    """Load a single skill from a SKILL.md file."""
    try:
        content = skill_path.read_text(encoding='utf-8')
        metadata, body = _parse_frontmatter(content)

        if not metadata.get('name'):
            return None

        return Skill(
            name=metadata['name'],
            description=metadata.get('description', ''),
            mode=metadata.get('mode', 'all'),
            instructions=body.strip(),
            allowed_agents=metadata.get('allowed_agents', ['THINKER', 'PLANNER', 'EXECUTOR', 'REVIEWER']),
            requires=metadata.get('requires', {}),
        )
    except Exception:
        return None


def load_skills_from_dir(
    skills_dir: Path,
    *,
    exclude_subdirs: list[str] | None = None,
) -> list[Skill]:
    """Load all skills from a directory (recursively finds SKILL.md files).

    ``exclude_subdirs`` skips any path that passes through one of the named
    subdirectories (e.g. ``["pending"]`` to avoid loading unapproved
    candidates).
    """
    skills = []
    if not skills_dir.exists():
        return skills

    excluded = {name.strip("/") for name in (exclude_subdirs or [])}

    for skill_file in skills_dir.rglob('SKILL.md'):
        if excluded:
            try:
                rel_parts = skill_file.relative_to(skills_dir).parts
            except ValueError:
                rel_parts = skill_file.parts
            if any(part in excluded for part in rel_parts):
                continue
        skill = load_skill(skill_file)
        if skill:
            skills.append(skill)

    return skills
