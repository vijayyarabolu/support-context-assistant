---
id: rb-001
title: API gateway timeouts (ERR-504)
error_codes: [ERR-504]
category: networking
severity: high
---
## Symptoms
Clients receive HTTP 504 Gateway Timeout from the public API. Checkout and order endpoints are usually hit first because they call the payments service synchronously.

## Likely causes
The upstream service took longer than the 29 second gateway limit. Common reasons are a slow database query, a cold connection pool after a deploy, or a downstream payment provider that is degraded.

## Resolution steps
1. Check tenant logs for ERR-504 and note which route and upstream service appear most often.
2. Look for DB-1205 lock timeouts in the same window. If present, follow rb-002 first, because lock waits are the most common cause of gateway timeouts.
3. If timeouts started right after a deploy, confirm the connection pool warmed up. Restart the service with a pool pre-warm if it did not.
4. If the payments upstream is slow, check the provider status page and enable the async checkout fallback for the tenant.

## Escalation
Enterprise tenants with more than 50 timeouts in an hour page the on-call SRE. Other tiers open a P2 ticket.
