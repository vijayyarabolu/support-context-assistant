---
id: rb-009
title: Duplicate records from sync conflicts (SYNC-409)
error_codes: [SYNC-409]
category: integrations
severity: medium
---
## Symptoms
CRM sync creates duplicate contacts and logs show SYNC-409 conflict on upsert.

## Likely causes
Two webhooks for the same record arrived within milliseconds and both tried to create it, or the integration does not send an idempotency key.

## Resolution steps
1. Confirm the integration sends an Idempotency-Key header on create calls.
2. Enable the dedupe window of 10 seconds for the tenant's sync connector.
3. Run the duplicate merge job for affected records.

## Escalation
If duplicates continue after dedupe is enabled, open a ticket with the integrations team.
