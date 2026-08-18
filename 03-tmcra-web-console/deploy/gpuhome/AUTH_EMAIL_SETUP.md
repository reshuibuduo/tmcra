# TMCRA Account Email Setup

This runbook separates automated account email from human-operated mailboxes.

- Transactional sender: `no-reply@auth.tmcra.com` through a dedicated Resend key.
- Promotional sender: `updates@auth.tmcra.com` through a separate Resend key.
- Human mailboxes: `admin@tmcra.com`, `support@tmcra.com`, and
  `security@tmcra.com` through a mailbox provider such as Zoho Mail.
- Website accounts: email plus password, with a six-digit verification code.
- Optional federated login: Google OpenID Connect.

Do not use an employee mailbox or a manually operated mail client to send
registration or password-reset codes.

## 1. Configure Resend

1. Create a Resend account.
2. Add the sending domain `auth.tmcra.com`.
3. Add the exact SPF and DKIM records shown by Resend to the authoritative DNS
   zone. Do not copy example record values from documentation.
4. Wait for the Resend domain status to become verified.
5. Create two server-only, domain-scoped, sending-only API keys. Use one for
   transactional account email and one for promotional email. Never commit
   either key to this repository.
6. Configure the production environment:

```dotenv
TMCRA_MAIL_TRANSPORT=smtp
TMCRA_MAIL_FROM_EMAIL=no-reply@auth.tmcra.com
TMCRA_MAIL_FROM_NAME=TMCRA
TMCRA_MAIL_REPLY_TO=
TMCRA_SMTP_HOST=smtp.resend.com
TMCRA_SMTP_PORT=465
TMCRA_SMTP_SECURITY=smtps
TMCRA_SMTP_USERNAME=resend
TMCRA_SMTP_PASSWORD=<server-only-resend-api-key>
TMCRA_SMTP_TIMEOUT_SECONDS=15
TMCRA_EMAIL_TOKEN_SECONDS=600
TMCRA_PASSWORD_RESET_SECONDS=1800

TMCRA_MARKETING_ENABLED=true
TMCRA_MARKETING_MAIL_TRANSPORT=smtp
TMCRA_MARKETING_MAIL_FROM_EMAIL=updates@auth.tmcra.com
TMCRA_MARKETING_MAIL_FROM_NAME=TMCRA
TMCRA_MARKETING_MAIL_REPLY_TO=
TMCRA_MARKETING_SMTP_HOST=smtp.resend.com
TMCRA_MARKETING_SMTP_PORT=465
TMCRA_MARKETING_SMTP_SECURITY=smtps
TMCRA_MARKETING_SMTP_USERNAME=resend
TMCRA_MARKETING_SMTP_PASSWORD=<separate-server-only-resend-api-key>
TMCRA_MARKETING_SMTP_TIMEOUT_SECONDS=15
TMCRA_MARKETING_API_TOKEN=<random-server-only-bearer-token>
TMCRA_MARKETING_SEND_INTERVAL_SECONDS=0.25
TMCRA_MARKETING_MAX_ATTEMPTS=3
```

Set `TMCRA_MAIL_REPLY_TO=support@tmcra.com` only after that mailbox exists and
has passed an inbound-mail test.

## 2. Configure human mailboxes

Use a mailbox provider independently from the transactional sender. Add and
verify `tmcra.com`, then create the primary administrator mailbox and support
and security aliases. The root-domain MX records belong to the mailbox
provider. Resend remains isolated on `auth.tmcra.com`.

Keep one valid SPF policy per DNS name. Do not merge the root-domain mailbox
SPF record into the `auth.tmcra.com` Resend policy.

## 3. Configure Google login

Create a Google OAuth client with application type `Web application`.

- Authorized JavaScript origin: `https://tmcra.com`
- Authorized redirect URI: `https://tmcra.com/oauth/google/callback`

Store the client credentials only in the production environment:

```dotenv
TMCRA_GOOGLE_CLIENT_ID=<client-id>
TMCRA_GOOGLE_CLIENT_SECRET=<client-secret>
TMCRA_GOOGLE_REDIRECT_URI=https://tmcra.com/oauth/google/callback
```

The callback requires a one-time state value, a browser-bound secure cookie,
PKCE S256, and a provider profile with `email_verified=true`.

## 4. Deployment gates

Do not activate the release until all of the following pass:

1. `python -m py_compile deploy/gpuhome/proxy.py`
2. `python -m unittest tests/test_gpuhome_proxy.py`
3. `npm test`
4. The Resend domain is verified and a real test email reaches an external
   mailbox.
5. `GET /__deployment/health` reports `registrationReady: true`.
   When promotional email is enabled it must also report
   `marketingReady: true`.
6. A clean browser can register, receive a code, verify, sign out, sign in,
   request a password reset, and sign in with the new password.
7. If Google is enabled, the health response reports `googleLogin: true` and a
   clean-browser Google login succeeds.
8. Queue a campaign with no opted-in users and verify that it completes with
   zero recipients and sends no email. Then opt in one test account, queue a
   new idempotent campaign, verify one delivery, and confirm that one-click
   unsubscribe prevents the next campaign from selecting that account.

Back up the SQLite account database before removing test accounts. Delete test
accounts only after the new release has passed the real-email smoke test.

## 5. Failure handling

- SMTP failure returns a temporary-unavailable response and does not create a
  verified session.
- Verification and reset codes are stored as HMAC digests, expire, are
  single-use, and stop accepting attempts after the configured limit.
- Password reset revokes existing sessions in the same SQLite transaction.
- Do not log OAuth codes, state values, passwords, or verification codes.
- Campaign requests cannot supply arbitrary recipient addresses. The server
  derives the audience from verified accounts with a current opt-in.
- Campaign submission is asynchronous and idempotent. A delivery rechecks
  consent immediately before SMTP transmission and skips an opted-out user.
