---
id: rb-006
title: Payment webhook failures (PAY-402)
error_codes: [PAY-402]
category: billing
severity: high
---
## Symptoms
Invoices stay unpaid after a successful card charge, and logs show PAY-402 webhook signature verification failed.

## Likely causes
The webhook signing secret was rotated at the payment provider but not updated in tenant settings, or a proxy is modifying the request body.

## Resolution steps
1. Confirm the signing secret in tenant billing settings matches the one shown in the provider dashboard.
2. Replay failed webhook events from the provider dashboard after updating the secret.
3. Reconcile invoices that were charged but not marked paid.

## Escalation
If charges succeeded but invoices remain unpaid for more than 24 hours, involve the billing team.
