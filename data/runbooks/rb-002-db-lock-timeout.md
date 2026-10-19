---
id: rb-002
title: Database lock wait timeouts (DB-1205)
error_codes: [DB-1205]
category: database
severity: high
---
## Symptoms
Writes fail with DB-1205 lock wait timeout exceeded. Users see slow saves, and the API may return ERR-504 when requests wait on the lock.

## Likely causes
A long running transaction holds row locks. This is often a bulk import or a report query that runs inside a transaction during business hours.

## Resolution steps
1. Find the blocking transaction in the database process list and record its query and start time.
2. If it is a tenant bulk import, pause the import job and rerun it in smaller batches of 500 rows.
3. If it is a report, move it to the read replica.
4. Confirm DB-1205 errors stop within five minutes after the blocker is cleared.

## Escalation
If no blocking transaction is visible, escalate to the database team with the lock wait graph.
