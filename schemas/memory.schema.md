# Domain memory layout — `.ai/memory/domains/<domain>/`

```
MEMORY.md                  index only, under context.memory_index_max_lines
purpose.md                 what this domain is for, and for whom
architecture.md            components, boundaries, data flow
user-journeys.md           verified end-to-end flows
important-files.md         entry points and where to change what
contracts.md               APIs, events, data shapes
permissions.md             who may do what, enforced where
testing.md                 verified commands to test this domain
known-risks.md             recurring problems, gotchas, dead ends
review-history.md          past findings, so they are not rediscovered
```

`MEMORY.md` is a table of contents plus current critical risks. It is loaded when the
domain steward starts; everything else is loaded only when needed.

Write durable, verified facts only. If it might be wrong, mark it `UNVERIFIED`.
If it is transient, it does not belong in memory.
