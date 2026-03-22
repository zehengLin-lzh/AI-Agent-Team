---
name: system-design
description: System architecture design patterns and principles
mode: architecture
allowed_agents: [THINKER, PLANNER, EXECUTOR, REVIEWER]
---

# System Architecture Design

## Design Principles
- **Separation of Concerns**: Each component has one clear responsibility
- **Loose Coupling**: Components interact through well-defined interfaces
- **High Cohesion**: Related functionality is grouped together
- **DRY**: Don't repeat yourself, but don't abstract prematurely
- **YAGNI**: You aren't gonna need it — build for today's requirements
- **KISS**: Keep it simple — complexity is the enemy of reliability

## Architecture Patterns
- **Layered**: UI → Business Logic → Data Access → Database
- **Microservices**: Independent services communicating via APIs
- **Event-Driven**: Components communicate through events/messages
- **CQRS**: Separate read and write models
- **API Gateway**: Single entry point for all client requests

## Data Architecture
- Choose storage based on access patterns, not popularity
- Design schemas for query patterns, not just data structure
- Plan for data migration from day one
- Consider caching strategy early
- Define data ownership boundaries

## API Design
- Use consistent naming conventions
- Version APIs from the start
- Design for backwards compatibility
- Document all endpoints with examples
- Use proper HTTP methods and status codes
- Implement rate limiting and pagination

## Security Architecture
- Defense in depth: multiple layers of security
- Principle of least privilege
- Input validation at every boundary
- Encrypt data at rest and in transit
- Audit logging for sensitive operations

## Scalability Considerations
- Identify potential bottlenecks early
- Design stateless services where possible
- Plan horizontal scaling strategy
- Consider async processing for heavy tasks
- Monitor and set alerts for resource usage
