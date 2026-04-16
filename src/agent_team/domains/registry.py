"""Domain registry — auto-detects the best domain plugin for a task."""
from __future__ import annotations

from agent_team.domains.base import DomainPlugin
from agent_team.domains.coding import CodingPlugin
from agent_team.domains.writing import WritingPlugin
from agent_team.domains.research import ResearchPlugin
from agent_team.domains.data import DataPlugin
from agent_team.domains.general import GeneralPlugin


# All built-in domain plugins, ordered by specificity (most specific first)
_BUILTIN_PLUGINS: list[DomainPlugin] = [
    CodingPlugin(),
    DataPlugin(),
    ResearchPlugin(),
    WritingPlugin(),
    GeneralPlugin(),  # Always last — catch-all
]


class DomainRegistry:
    """Manages domain plugins and selects the best one for a task."""

    def __init__(self, plugins: list[DomainPlugin] | None = None):
        self._plugins = plugins or list(_BUILTIN_PLUGINS)

    def detect(self, request: str) -> DomainPlugin:
        """Find the best domain plugin for a request.

        Runs all plugins' detect() methods and picks the highest scorer.
        Falls back to GeneralPlugin if all scores are tied.
        """
        scored = [(p, p.detect(request)) for p in self._plugins]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    def detect_with_scores(self, request: str) -> list[tuple[DomainPlugin, float]]:
        """Return all plugins with their detection scores, sorted descending."""
        scored = [(p, p.detect(request)) for p in self._plugins]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def get_plugin(self, name: str) -> DomainPlugin | None:
        """Get a specific plugin by name."""
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def list_plugins(self) -> list[str]:
        """List all registered plugin names."""
        return [p.name for p in self._plugins]

    def register(self, plugin: DomainPlugin) -> None:
        """Register a custom domain plugin (inserted before GeneralPlugin)."""
        # Insert before the last plugin (GeneralPlugin)
        self._plugins.insert(-1, plugin)


def get_domain_for_task(request: str, forced_domain: str = "") -> DomainPlugin:
    """Convenience function: detect or force a domain for a task.

    Args:
        request: The user's task/request text.
        forced_domain: If set, use this domain regardless of detection.
    """
    registry = DomainRegistry()
    if forced_domain:
        plugin = registry.get_plugin(forced_domain)
        if plugin:
            return plugin
    return registry.detect(request)
