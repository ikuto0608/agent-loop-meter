# Example: a small shop website, fixed by sub-agents

One Claude Code control-tower session spawned sub-agents that each fixed one small thing on a small
shop's website. One task = from spawning the agent to confirming the change live. This is the data the
dashboard's "before" strip was built from.

Data: [`before-tasks.csv`](before-tasks.csv) (one row per task; titles generalised to a category).
Show it in the dashboard:

```
node dashboard/server.mjs --before examples/small-shop-site/before-tasks.csv
```

## Numbers (3 days, 55 tasks)

| item | value |
|---|---|
| time per task | median **74 min** (middle half 52-104), mean 81, shortest 32, longest 195 |
| rows | 55 (28 from the agents' own reports, 27 estimated from timestamps) |

### Share of time by stage (the 28 reported tasks, 2,505 min total)

| stage | share |
|---|---|
| implementation | 33% |
| tests | 37% |
| queueing (a lock for heavy tests) | 13% |
| other (interruptions, extra instructions, waiting on another PR, CI) | 8% |
| review | 5% |
| deploy wait (merge to live version, about 4 min each) | 5% |

### Top 3 causes of waiting

1. **Contention for the heavy-test lock.** Heavy tests ran one at a time on one machine, guarded by a lock
   that 2-3 parallel agents fought over. Waits occurred in 13 of 28 tasks: mean 25 min, max 70 min.
2. **External interruptions.** Network down for 30 min, a CI check stuck for 10 min, no login to the deploy target.
3. **Waiting on another PR, extra instructions mid-task, re-running tests after a rebase.**

### What we did about it

- **Pick tests from the changed files.** A mapping table narrows the run; anything not in the table, or touching
  shared foundations, falls back to the full suite. Full e2e about 16 min -> about 7 min when it narrowed
  (the later measurement is only 2 runs, indicative).
- **Make one kind of change a single command.** A map change went from 195 min to 70 min.

## Caveats

The 27 estimated rows cannot see queueing or review (they fold into implementation), so waiting is a lower bound.
Shares use the 28 reported rows only. Only 2 "after" runs exist.
