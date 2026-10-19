---
id: rb-012
title: Custom domain TLS certificate expired (TLS-495)
error_codes: [TLS-495]
category: networking
severity: high
---
## Symptoms
The tenant's custom domain shows a certificate warning, and logs show TLS-495 certificate expired or not yet validated.

## Likely causes
Automatic renewal failed because the DNS validation record was removed from the tenant's DNS.

## Resolution steps
1. Check whether the CNAME validation record still exists in the tenant's DNS.
2. Send the tenant admin the validation record to add back.
3. Trigger certificate renewal once DNS propagates.

## Escalation
If the record exists and renewal still fails, escalate to the networking team.
