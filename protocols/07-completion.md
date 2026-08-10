# Protocol 07 — Completion

## Done means all of these

- [ ] Every acceptance criterion observed to pass
- [ ] Every mandatory quality dimension addressed with evidence
- [ ] All BLOCKING review findings resolved
- [ ] No unresolved fabrication that this task depends on
- [ ] Documentation reflects reality
- [ ] Memory and decisions recorded
- [ ] Verification statuses recorded honestly
- [ ] Locks released, state files updated, work committed with attribution

Anything unchecked means the status is `INCOMPLETE` or `PARTIAL`, and it is reported
as such. There is no partial credit awarded by optimism.

## Report format

```
STATUS: COMPLETE | PARTIAL | BLOCKED | INCOMPLETE

REQUEST        <what was asked, restated as the objective>
TASKS          <ids, status each>
CHANGED        <files, grouped by area>

ACCEPTANCE     <criterion -> observed result>
VERIFICATION   <command -> status>
DIMENSIONS     <dimension -> evidence or NOT_RUN>
REVIEW         BLOCKING: n (resolved n) | IMPORTANT: n | OPTIONAL: n
FABRICATIONS   <still fake, and what replaces it>

NOT VERIFIED   <what you did not check, plainly>
RISKS          <what could still be wrong>
HUMAN ACTIONS  <exact steps only a human can perform>
NEXT           <ready task ids>
```

Never write "everything works", "fully tested", or "production ready" unless every
line above supports it. Confident language on unverified work is the failure mode
this framework exists to eliminate.
