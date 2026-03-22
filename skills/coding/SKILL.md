---
name: coding-best-practices
description: Python and web development coding best practices and patterns
mode: coding
allowed_agents: [THINKER, PLANNER, EXECUTOR, REVIEWER]
---

# Coding Best Practices

## Python
- Use type hints for function signatures
- Prefer f-strings over .format() or % formatting
- Use pathlib.Path instead of os.path
- Use dataclasses or Pydantic models for structured data
- Handle exceptions specifically, never bare `except:`
- Use `async/await` for I/O-bound operations
- Follow PEP 8 naming conventions

## Web Development
- Validate all user inputs server-side
- Use parameterized queries to prevent SQL injection
- Set appropriate CORS policies
- Return proper HTTP status codes
- Handle error responses consistently with error details
- Use environment variables for configuration, never hardcode secrets

## File Structure
- Group related functionality into modules
- Keep files focused — one primary responsibility per file
- Use __init__.py to expose public API
- Separate concerns: routing, business logic, data access

## Testing
- Write tests for critical paths first
- Test both happy path and error cases
- Use fixtures for test data setup
- Mock external dependencies, not internal logic
