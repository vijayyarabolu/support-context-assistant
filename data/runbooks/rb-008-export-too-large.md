---
id: rb-008
title: Report exports fail with payload too large (EXPORT-413)
error_codes: [EXPORT-413]
category: reporting
severity: low
---
## Symptoms
CSV or PDF exports fail with EXPORT-413 payload too large.

## Likely causes
The export exceeds the 100 MB synchronous limit, usually because the date range covers several years.

## Resolution steps
1. Ask the user to narrow the date range or add filters.
2. For large exports, switch the user to scheduled exports, which deliver a download link by email.

## Escalation
None needed unless scheduled exports also fail.
