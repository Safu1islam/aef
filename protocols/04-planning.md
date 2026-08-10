# Protocol 04 — Planning

A plan that lives in a chat message is not a plan. It is a suggestion the next agent
will reinterpret. Plans are written to `.ai/state/tasks.yaml` as a graph.

## Steps

1. Classify the change against `config/routing.yaml`. Apply escalators. The resulting
   mode and mandatory dimensions are not negotiable by you.
2. Decompose into tasks that are independently completable and independently verifiable.
3. Establish interface contracts **before** dependent tasks are created. Two agents
   building against an unwritten contract will produce two incompatible halves.
4. Assign owned paths per task. Overlapping ownership means the decomposition is wrong.
5. Write acceptance criteria as observable behaviour, before implementation.
6. Attach verification commands per task.
7. Order by dependency; mark what can run in parallel.

## Acceptance criteria

Good: "Submitting the form with an email already in use shows an inline message on the
email field, preserves all other entered values, and creates no record."

Bad: "Registration works." / "The page loads." / "It runs without errors."

If a criterion cannot be observed from outside the code, rewrite it.

## Sizing

A task that cannot be finished within one session's context budget is too large.
Split it. Resumability beats ambition.
