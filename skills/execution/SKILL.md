---
name: safe-execution
description: Safe code execution practices and testing approaches
mode: execution
allowed_agents: [THINKER, PLANNER, EXECUTOR, REVIEWER]
---

# Safe Execution Practices

## Before Execution
- Verify all dependencies are available
- Check file permissions and disk space
- Validate environment variables are set
- Review commands for destructive operations
- Set appropriate timeouts

## During Execution
- Capture both stdout and stderr
- Monitor resource usage (memory, CPU)
- Log all commands and their outputs
- Handle signals gracefully (SIGTERM, SIGINT)
- Use non-zero exit codes for failures

## After Execution
- Verify expected outputs exist
- Check exit codes
- Clean up temporary files
- Report results clearly
- Suggest fixes for failures

## Testing Strategy
- Start with the simplest test case
- Test one thing at a time
- Verify both success and failure paths
- Use assertions with clear messages
- Clean up test artifacts

## Rollback Planning
- Know what to undo if execution fails
- Keep backups of modified files
- Document manual recovery steps
- Test rollback procedures
