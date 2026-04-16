"""Domain plugin system — makes the agent team general-purpose."""
from agent_team.domains.registry import DomainRegistry, get_domain_for_task

__all__ = ["DomainRegistry", "get_domain_for_task"]
