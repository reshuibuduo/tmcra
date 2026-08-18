# Security Policy

## Supported version

Security fixes are made against the latest commit on the default branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use this repository's
private vulnerability-reporting channel when it is enabled, or contact the
maintainers through the private channel listed in the repository's GitHub
profile or organization page. Include a minimal reproduction and impact
description. Do not include credentials, customer data, production logs, or
full database exports.

## Secrets and deployment data

This release is curated to exclude provisioned provider credentials, server
private keys, databases, customer records, and production logs. Configuration
examples contain placeholders only. Operators must supply their own credentials
through environment files kept outside source control.
