---
id: rb-003
title: Expired or invalid access tokens (AUTH-401)
error_codes: [AUTH-401]
category: authentication
severity: medium
---
## Symptoms
API calls return 401 Unauthorized with AUTH-401. Users are logged out unexpectedly or integrations stop syncing.

## Likely causes
The access token expired and the client did not refresh it, the refresh token was revoked after a password reset, or the client clock is skewed.

## Resolution steps
1. Check whether failures started after a password reset or an admin revoked sessions.
2. Ask the integration owner to confirm the client refreshes tokens before the one hour expiry.
3. If only one integration fails, rotate its client secret and reauthorize it.

## Escalation
If every user in the tenant gets AUTH-401 at once, treat it as an identity provider outage and escalate to the platform team.
