---
id: rb-010
title: Background jobs stuck in the dead letter queue (QUEUE-DLQ)
error_codes: [QUEUE-DLQ]
category: jobs
severity: medium
---
## Symptoms
Emails, notifications or imports never complete, and the tenant has messages in the dead letter queue with QUEUE-DLQ.

## Likely causes
A job failed five times in a row, usually because of bad input data or an expired third party credential.

## Resolution steps
1. Read the last error on a sample of dead letter messages to find the common cause.
2. Fix the input or credential, then redrive the messages back to the main queue.
3. Watch that the queue drains and no new messages land in the dead letter queue.

## Escalation
More than 10,000 dead letter messages for one tenant goes to the platform team before redriving.
