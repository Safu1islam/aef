# Why AEF exists

Nine failures, observed repeatedly, each with a mechanism rather than an exhortation.

### 1. Every agent invents its own process
Ask three tools to build the same thing and you get three architectures, three
conventions, three ideas of "done". Fixed by a constitution plus routing data: process
is read from configuration, not improvised per session.

### 2. A new chat rediscovers everything
Context dies with the session, so the next agent re-reads the repository to learn what
the last one already knew. Fixed by making the repository the memory: state files,
decision records, domain memory indexes.

### 3. The wrong stack, chosen by fluency
An agent asked for a latency-critical system reaches for whatever it writes most
fluently, because nobody wrote down a latency budget. The mismatch surfaces after the
system exists, and the user pays for a rewrite. Fixed by a selection gate that requires
quantified constraints first, disqualification before selection, and an explicit
fluency-bias check.

### 4. "It runs, but nothing works"
The demo launches and every feature behind it is hollow. Fixed by acceptance criteria
written as observable behaviour before implementation, and independent verification
that executes rather than asserts.

### 5. Fake data indistinguishable from real
Agents fabricate scaffolding to make a demo run and never record it. Months later
nobody can tell invented values from real ones. Fixed by a fabrication registry written
at creation time, with completion blocked while dependencies are still fake.

### 6. Simple output where serious output was needed
Rollback plans, retention policies, threat models, observability, capacity — the things
that separate a product from a demo are exactly what an agent silently skips. Fixed by
mandatory quality dimensions computed per change class, not left to the agent's sense
of how much the user probably wants.

### 7. Token burn and lost usage limits
Agents re-read everything, run frontier models on mechanical work, and lose a day's
progress when a limit hits mid-task. Fixed by tiered loading, model tiering per task
class, and checkpointed state that any session can resume.

### 8. Long sessions degrade
An agent a million tokens deep is worse than the same agent fresh. Fixed by capping
session length and putting continuity in files rather than context, so a fresh session
is cheap instead of costly.

### 9. No accountability
When something is wrong, there is no way to know which agent produced it or under what
task. Fixed by role, task, and model attribution on every commit.

---

None of these are solved by telling an agent to try harder. Each needs a mechanism with
an artifact, and an artifact a human can check.
