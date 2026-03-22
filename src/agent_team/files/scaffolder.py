"""Plan path scaffolding -- create directory/file structure from PLANNER output."""
import re
from pathlib import Path
from agent_team.files.writer import _resolve_base_dir

def extract_plan_file_paths(planner_output: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"Step\s+\d+:.*?\u2192\s*([^\s\u2192,]+)\s*\u2192", planner_output):
        candidate = match.group(1).strip()
        if "/" in candidate:
            paths.append(candidate)
    for match in re.finditer(r"[\u2502\u251c\u2514\u2500\s]+([\w.\-/]+(?:/[\w.\-/]+)+)", planner_output):
        candidate = match.group(1).strip()
        if candidate and all(len(seg) > 1 for seg in candidate.split("/")):
            paths.append(candidate)
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique

def scaffold_plan_paths(
    planner_output: str,
    execution_path: str | None = None,
) -> tuple[list[Path], list[Path]]:
    base_dir = _resolve_base_dir(execution_path)
    created: list[Path] = []
    existing: list[Path] = []
    for raw in extract_plan_file_paths(planner_output):
        clean = raw.lstrip("/")
        if not clean:
            continue
        target = base_dir / clean
        if target.exists():
            existing.append(target)
        else:
            if raw.endswith("/") or "." not in target.name:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            created.append(target)
    return created, existing
