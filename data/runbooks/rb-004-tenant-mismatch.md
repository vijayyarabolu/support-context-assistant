---
id: rb-004
title: Tenant mismatch on authorized requests (AUTH-403)
error_codes: [AUTH-403]
category: authentication
severity: high
---
## Symptoms
Requests fail with AUTH-403 tenant mismatch. The token is valid but belongs to a different tenant than the resource being requested.

## Likely causes
A user belongs to more than one workspace and the client cached the wrong tenant, or an API key from a sandbox tenant is being used against production.

## Resolution steps
1. Compare the tenant claim in the token with the tenant in the request path.
2. If the user has several workspaces, ask them to switch workspace and sign in again.
3. If a sandbox key is used in production, issue a production key and revoke the old one.

## Escalation
Never bypass the check. Repeated AUTH-403 from unknown IP addresses goes to the security team.
