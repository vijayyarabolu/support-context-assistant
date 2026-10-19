---
id: rb-011
title: File download links expired (S3-403)
error_codes: [S3-403]
category: storage
severity: low
---
## Symptoms
Users clicking file or attachment links get S3-403 access denied or request has expired.

## Likely causes
Presigned download links are valid for 15 minutes. Links copied into email or chat expire before the recipient opens them.

## Resolution steps
1. Ask the user to open the file from inside the app, which generates a fresh link.
2. For shared files, use the share feature, which creates a link valid for 7 days.

## Escalation
If fresh links also fail, check the storage bucket policy with the platform team.
