---
id: rb-005
title: Rate limiting and throttled requests (RATE-429)
error_codes: [RATE-429]
category: api
severity: medium
---
## Symptoms
Clients receive 429 Too Many Requests with RATE-429. Integrations slow down or drop updates during peak hours.

## Likely causes
The tenant exceeded its plan limit. Starter allows 10 requests per second, Pro allows 50 and Enterprise allows 200. Retry loops without backoff often multiply the problem.

## Resolution steps
1. Check the request rate for the tenant and identify the client or API key sending the most traffic.
2. Ask the integration owner to add exponential backoff and honor the Retry-After header.
3. For a short spike on Pro or Enterprise, support can grant a 24 hour temporary limit increase.
4. For sustained traffic above the plan limit, route the tenant to their account manager for an upgrade.

## Escalation
Temporary increases above 2x the plan limit need approval from the platform team.
