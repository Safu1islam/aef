# Protocol 06 — Verification

Verification is execution, not assertion.

## Sequence

1. Run every verification command on the task record.
2. Run the checks required by the task's mandatory quality dimensions.
3. For each, record: command, exit code, observed result, status.
4. If a required check does not exist, create it. That is part of the task.

## Statuses

| Status | Meaning |
|---|---|
| PASSED | Executed. Succeeded. Observed. |
| FAILED | Executed. Did not succeed. |
| NOT_AVAILABLE | No such check exists in this project. |
| NOT_RUN | Exists, was not executed. Say why. |
| BLOCKED | Could not execute — environment, credentials, dependency. |
| NEEDS_HUMAN | Requires human judgement or access. |

`NOT_RUN` is honest. A false `PASSED` is the most damaging act available to an agent
in this framework, because every downstream decision inherits the lie.

## Prohibited

Weakening assertions, deleting tests, narrowing scope to green, marking skipped as
passing, or reporting aggregate success while a component failed.
