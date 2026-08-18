# TMCRA Mail APIs / TMCRA 邮件 API

These contracts belong to the website gateway. They are separate from the
public memory API and use the canonical origin `https://tmcra.com`.

这些接口属于官网网关，不属于对外记忆 API；生产环境固定使用
`https://tmcra.com`。

## 1. Email verification / 邮箱验证

`POST /api/auth/v1/email-verifications`

Requests a new six-digit code for an existing, unverified account. The request
must be same-origin JSON. A missing, verified, and pending account all receive
the same `202` response, so the endpoint does not disclose account existence.

为已有但尚未验证的账户请求新的六位验证码。请求必须来自同源网页并使用
JSON。不存在、已验证和待验证账户均返回相同的 `202`，避免账户枚举。

```json
{"email":"person@example.com"}
```

```json
{"ok":true,"status":"accepted"}
```

`POST /api/auth/v1/email-verifications/confirm`

Consumes the code once, verifies the email, and creates the secure website
session cookie.

一次性消费验证码，完成邮箱验证并创建安全的网站会话 Cookie。

```json
{"email":"person@example.com","code":"123456"}
```

Both endpoints enforce per-IP and per-account rate limits. Codes are stored as
HMAC digests, expire after ten minutes by default, and are never logged.

两个接口均执行 IP 与账户双重限流。验证码仅以 HMAC 摘要保存，默认十分钟
过期，且禁止写入日志。

## 2. Marketing preference / 推广邮件偏好

`GET /api/account/v1/email-preferences`

Returns the current preference for the authenticated website account.

返回当前已登录官网账户的邮件偏好。

`PUT /api/account/v1/email-preferences`

The request must be same-origin JSON and use the authenticated website session.

请求必须为同源 JSON，并携带已登录的网站会话。

```json
{"marketing":true}
```

Account and security email is transactional and cannot be disabled through the
marketing preference. Promotional email always requires an explicit opt-in.

账户和安全邮件属于事务邮件，不受推广偏好影响；推广邮件必须由用户明确订阅。

## 3. Campaign API / 推广活动 API

`POST /internal/email/v1/campaigns`

This server-only endpoint requires
`Authorization: Bearer <TMCRA_MARKETING_API_TOKEN>`. It accepts content and an
idempotency key, but never accepts recipient email addresses. The only supported
audience is `all_opted_in`; the server selects verified accounts whose latest
preference is opt-in.

该接口仅供服务器调用，必须使用
`Authorization: Bearer <TMCRA_MARKETING_API_TOKEN>`。接口只接受内容与幂等键，
不接受收件人邮箱列表。当前唯一受支持的受众为 `all_opted_in`，由服务器从已验证
且最新状态为订阅的账户中选择。

```json
{
  "idempotency_key":"launch-2026-07-17",
  "audience":"all_opted_in",
  "subject":"TMCRA launch update",
  "text_body":"The launch is ready.",
  "html_body":"<p>The launch is ready.</p>"
}
```

The response is `202` for a newly queued campaign and `200` when the identical
idempotency key and payload already exist. Reusing a key with different content
returns `409`.

新活动入队返回 `202`；相同幂等键和相同内容重复提交返回 `200`；相同键配不同内容
返回 `409`。

`GET /internal/email/v1/campaigns` lists the latest 50 campaigns.

`GET /internal/email/v1/campaigns/{campaign_id}` returns counters and state.

The worker rechecks email verification and consent immediately before each
SMTP send. It appends an unsubscribe footer and sends RFC 8058
`List-Unsubscribe` headers. Queue state and delivery attempts survive restarts.

Worker 会在每次 SMTP 发送前再次检查邮箱验证与订阅状态，自动追加退订页脚并发送
RFC 8058 `List-Unsubscribe` 头。队列状态和发送次数在服务重启后仍然保留。

## 4. Unsubscribe / 一键退订

`GET /email/unsubscribe?token=...` shows a confirmation page and does not change
state. `POST /email/unsubscribe?token=...` performs the unsubscribe. Email
providers may submit `List-Unsubscribe=One-Click` in the form body.

`GET /email/unsubscribe?token=...` 只显示确认页，不改变状态；
`POST /email/unsubscribe?token=...` 执行退订。邮件服务商可在表单中提交
`List-Unsubscribe=One-Click`。

## 5. Operational boundaries / 运维边界

- Transactional and promotional SMTP credentials must be different.
- The marketing Bearer token is independent from TMCRA Account sessions and internal RBAC.
- Do not expose `/internal/email/v1/*` through third-party client SDKs.
- Never log API tokens, SMTP passwords, verification codes, or unsubscribe
  tokens.
- Rotate a leaked key at the provider, update `deployment.env` atomically, then
  restart the gateway and run both SMTP authentication checks.

- 事务邮件与推广邮件必须使用不同的 SMTP 凭据。
- 推广 Bearer Token 与 TMCRA 账户会话及内部 RBAC 完全独立。
- 禁止在第三方客户端 SDK 中暴露 `/internal/email/v1/*`。
- 禁止记录 API Token、SMTP 密码、验证码或退订 Token。
- 密钥泄露时，应先在服务商侧轮换，再原子更新 `deployment.env`，重启网关并检查
  两套 SMTP 登录。
