"""Trigger detection — match user input keywords to MCP servers and skills."""
import re
from dataclasses import dataclass, field

from agent_team.mcp.config import MCPConfig


# Common keyword → domain mappings for suggesting MCP servers/skills
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "database": [
        "database", "db", "sql", "sqlite", "postgres", "mysql",
        "mongodb", "query", "table", "schema", "migration", "orm",
    ],
    "filesystem": [
        "file", "directory", "folder", "read file", "write file",
        "create file", "delete file", "move file", "copy file", "path",
    ],
    "git": [
        "git", "commit", "branch", "merge", "pull request", "pr",
        "repo", "repository", "version control", "diff",
    ],
    "web": [
        "web", "http", "api", "rest", "graphql", "fetch",
        "scrape", "crawl", "browser", "url", "endpoint",
    ],
    "docker": [
        "docker", "container", "kubernetes", "k8s", "pod",
        "deployment", "image", "compose",
    ],
    "search": [
        "search", "find", "lookup", "index", "elasticsearch",
        "full-text search",
    ],
    "email": [
        "email", "mail", "smtp", "inbox", "send email",
    ],
    "cloud": [
        "aws", "gcp", "azure", "s3", "lambda", "cloud",
        "serverless", "terraform", "infrastructure",
    ],
    "data": [
        "csv", "json", "xml", "yaml", "parse", "transform",
        "etl", "data pipeline", "pandas", "dataframe",
    ],
    "testing": [
        "test", "unittest", "pytest", "coverage", "mock",
        "integration test", "e2e", "benchmark",
    ],
    "security": [
        "encrypt", "decrypt", "hash", "auth", "oauth",
        "jwt", "token", "certificate", "ssl", "tls",
    ],
    "monitoring": [
        "monitor", "log", "metric", "alert", "trace",
        "observability", "grafana", "prometheus",
    ],
}


@dataclass
class TriggerMatch:
    """A matched trigger with source information."""
    domain: str
    keywords_matched: list[str]
    server_name: str = ""       # MCP server that matches
    skill_name: str = ""        # Skill that matches
    confidence: float = 0.0     # 0.0 - 1.0


def detect_domains(text: str) -> list[tuple[str, list[str]]]:
    """Detect which domains are relevant to the given text.
    Returns list of (domain, matched_keywords) sorted by match count."""
    text_lower = text.lower()
    results = []

    for domain, keywords in DOMAIN_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in text_lower]
        if matched:
            results.append((domain, matched))

    results.sort(key=lambda x: len(x[1]), reverse=True)
    return results


def match_mcp_servers(text: str, config: MCPConfig) -> list[TriggerMatch]:
    """Match user input against configured MCP server triggers.
    Returns matches sorted by confidence."""
    text_lower = text.lower()
    matches = []

    for name, server in config.servers.items():
        if not server.enabled:
            continue

        matched_keywords = []

        # Check server-specific triggers
        for trigger in server.triggers:
            if trigger.lower() in text_lower:
                matched_keywords.append(trigger)

        # Check server name and description
        if name.lower() in text_lower:
            matched_keywords.append(name)
        for word in server.description.lower().split():
            if len(word) > 3 and word in text_lower:
                matched_keywords.append(word)

        if matched_keywords:
            # Remove duplicates
            matched_keywords = list(set(matched_keywords))
            confidence = min(1.0, len(matched_keywords) * 0.3)
            matches.append(TriggerMatch(
                domain=name,
                keywords_matched=matched_keywords,
                server_name=name,
                confidence=confidence,
            ))

    matches.sort(key=lambda x: x.confidence, reverse=True)
    return matches


def match_skills(text: str, skills_list: list[dict]) -> list[TriggerMatch]:
    """Match user input against available skills.
    skills_list: list of dicts with 'name', 'description', 'mode' keys."""
    text_lower = text.lower()
    matches = []

    for skill in skills_list:
        name = skill.get("name", "")
        desc = skill.get("description", "")
        matched = []

        if name.lower() in text_lower:
            matched.append(name)

        # Check description words
        for word in desc.lower().split():
            if len(word) > 3 and word in text_lower:
                matched.append(word)

        if matched:
            matched = list(set(matched))
            matches.append(TriggerMatch(
                domain=skill.get("mode", "all"),
                keywords_matched=matched,
                skill_name=name,
                confidence=min(1.0, len(matched) * 0.25),
            ))

    matches.sort(key=lambda x: x.confidence, reverse=True)
    return matches


def suggest_tools_for_request(
    text: str,
    config: MCPConfig,
    skills_list: list[dict],
) -> dict:
    """Analyze user request and suggest relevant MCP servers and skills.
    Returns a dict with 'domains', 'mcp_matches', 'skill_matches', 'suggestions'."""

    domains = detect_domains(text)
    mcp_matches = match_mcp_servers(text, config)
    skill_matches = match_skills(text, skills_list)

    suggestions = []

    # Suggest configured MCP servers that match
    for match in mcp_matches:
        suggestions.append({
            "type": "mcp",
            "name": match.server_name,
            "reason": f"Keywords matched: {', '.join(match.keywords_matched)}",
            "confidence": match.confidence,
        })

    # Suggest skills that match
    for match in skill_matches:
        suggestions.append({
            "type": "skill",
            "name": match.skill_name,
            "reason": f"Keywords matched: {', '.join(match.keywords_matched)}",
            "confidence": match.confidence,
        })

    # Suggest domains without configured tools (opportunity to install)
    configured_domains = {m.server_name.lower() for m in mcp_matches}
    for domain, keywords in domains:
        if domain not in configured_domains:
            suggestions.append({
                "type": "suggestion",
                "name": domain,
                "reason": f"Your request involves {domain} ({', '.join(keywords[:3])}). "
                          f"Consider adding an MCP server for this. Use /mcp search {domain}",
                "confidence": min(1.0, len(keywords) * 0.2),
            })

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return {
        "domains": domains,
        "mcp_matches": mcp_matches,
        "skill_matches": skill_matches,
        "suggestions": suggestions[:5],  # Top 5 suggestions
    }
