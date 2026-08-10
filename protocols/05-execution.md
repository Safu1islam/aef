# Protocol 05 — Execution

## Claim before you touch

Write a lock for every path you will modify. If it is held, take another ready task.
Never edit a locked path. Never hold a lock you are not actively using.

## While implementing

- Register every fabrication as you create it. Not at the end — at the moment.
- Follow recorded project conventions over personal preference.
- Externalise configuration. No hardcoded limits, endpoints, credentials, schedules,
  thresholds, or feature switches. If the project constitution names a domain rule as
  user-configurable, it must be configurable at runtime, not at build time.
- Fail loudly and recoverably.
- Keep units small enough to be understood alone.
- Do not expand scope. Log the improvement as a new task.

## Checkpoint

After each completed unit, update the task record and commit. A session that dies
between checkpoints must lose at most one unit of work.

## Commit

```
<type>(<scope>): <what changed>

<why, if not obvious>

AEF-Role: implementer
AEF-Task: T-014
AEF-Model: <model identifier>
```
