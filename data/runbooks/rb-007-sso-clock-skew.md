---
id: rb-007
title: SSO login failures from SAML clock skew (SSO-550)
error_codes: [SSO-550]
category: authentication
severity: medium
---
## Symptoms
Users cannot sign in through SSO and see SSO-550 assertion not yet valid or assertion expired.

## Likely causes
The identity provider clock is out of sync, or the SAML assertion lifetime is shorter than the allowed skew.

## Resolution steps
1. Compare the NotBefore and NotOnOrAfter timestamps in the failed assertion with server time.
2. Ask the tenant IT admin to enable NTP sync on their identity provider.
3. As a temporary measure, support can raise the allowed skew from 2 to 5 minutes for the tenant.

## Escalation
If skew is under 2 minutes and logins still fail, escalate with the raw assertion to the identity team.
