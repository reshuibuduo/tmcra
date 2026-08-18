# 为 Hermes 安装并核验 TMCRA

## 环境要求

- Python 3.10 或更高版本。
- 支持用户级 `MemoryProvider` 插件的 Hermes 版本。
- 已完成 TMCRA 设备授权，或持有只包含召回和写入权限的服务端受限凭证。

## 构建与安装

在本目录执行：

```bash
python -m pip install build
python -m build
python -m pip install dist/tmcra_hermes_plugin-0.4.1-py3-none-any.whl
tmcra-hermes install
```

`tmcra-hermes install` 会把插件复制到 `$HERMES_HOME/plugins/tmcra-hermes`，并在 `$HERMES_HOME/config.yaml` 中设置 `memory.provider: tmcra-hermes`。只安装 wheel 不会完成 Hermes 的插件激活。不要使用旧的 `plugins/memory/` 路径，也不要修改 Hermes 核心源码。

常用命令：

```bash
tmcra-hermes status
tmcra-hermes --hermes-home /custom/profile status
tmcra-hermes uninstall
```

`status` 只输出非敏感的安装状态。其中 `credentials_configured` 只检查 Hermes `.env` 内的两个凭证变量；即使它为 false，插件仍可能通过受保护的 TMCRA 设备授权正常工作。

## 授权与配置

普通用户登录 TMCRA 应用后，默认读取：

```text
~/.config/tmcra/config.json
~/.config/tmcra/installation.json
```

只有文件位于其他位置时，才设置 `TMCRA_CONFIG_FILE` 和 `TMCRA_INSTALLATION_FILE`。access token 只在运行时读取，不会复制进 Hermes 的 JSON 配置。

托管服务账号可以在 Hermes 服务进程中设置：

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_TENANT_ID='tenant-a'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
export TMCRA_IDENTITY_SECRET='stable-secret-at-least-16-characters'
export TMCRA_PROJECT_ID='shared-project-id'
```

随后运行 `hermes memory setup`，选择 `tmcra-hermes`，再重启 Hermes。插件只会把非敏感设置保存到 `$HERMES_HOME/tmcra-hermes.json`；凭证只能放在受保护的设备文件或 Hermes 进程环境中。

`TMCRA_PROJECT_ID` 是项目共享边界。同一项目的所有专业 Agent 必须使用同一值。`TMCRA_USER_ID` 可以指定用于派生全局 scope 的稳定本地用户标识；设备授权也可以直接提供服务端允许的 `globalScope` 和 `projectScopePrefix`。

可选运行参数：

```text
TMCRA_INCLUDE_GLOBAL_SCOPE=true
TMCRA_HERMES_QUEUE_PATH=/absolute/owner-only/pending-ingest.json
TMCRA_HTTP_TIMEOUT_SECONDS=5
TMCRA_MAX_CONTEXT_CHARS=32000
TMCRA_MAX_WINDOWS=8
TMCRA_MAX_ATTEMPTS=8
TMCRA_RETRY_BASE_SECONDS=2
TMCRA_RETRY_MAX_SECONDS=300
TMCRA_DRAIN_INTERVAL_SECONDS=60
TMCRA_MAX_PENDING_ITEMS=10000
```

当前 API 合约要求 `TMCRA_MAX_WINDOWS=8`。

## 全流程核验

```bash
python -m unittest discover -s tests -v
tmcra-hermes status
```

重启 Hermes 后，用一次性项目依次验证：

1. 回答前已经执行召回；TMCRA 召回暂时失败时，Agent 仍能回答。
2. 主 Agent 成功完成一轮后，项目共享 scope 中恰好写入一条 user 和一条 assistant 消息。
3. 两个 Agent 使用相同项目 ID 时 scope 相同、session 不同，Agent B 能召回 Agent A 已完成的项目进度。
4. 子 Agent 委派会以两条 assistant 记录保存父 Agent 的请求和子 Agent 的结果，并保留不同归属。
5. TMCRA 暂时不可用时，本地队列文件仍存在；服务恢复后能续传且不重复写入。

`tmcra-hermes-smoke` 会对真实服务执行召回、写入、等待任务完成和二次召回。只能使用受限凭证及一次性 scope，报告不会输出凭证或记忆正文。

本版本没有 Agent 私有召回开关；它是明确关闭的，不存在未公开的环境变量。
