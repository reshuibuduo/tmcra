# 为 OpenClaw 安装并核验 TMCRA

## 环境要求

- OpenClaw `2026.7.1-2` 或兼容的更高版本。
- 包声明支持范围内的 Node `22.22.3+`、`24.15+` 或 `25.9+`。
- 已完成 TMCRA 设备授权，或持有只包含召回和写入权限的服务端受限凭证。

## 构建、测试和打包

```bash
npm ci
npm test
npm run typecheck
npm pack --pack-destination artifacts
```

通过 OpenClaw 管理的 npm 安装路径安装真实 tarball：

```bash
openclaw plugins install \
  npm-pack:/absolute/path/tmcra-openclaw-memory-0.4.0.tgz --force
openclaw plugins enable tmcra-openclaw
```

`npm-pack:` 会核验托管安装所使用的包结构。链接源码目录只适合开发，不能作为发布包验收结果。

## 配置凭证

普通用户登录 TMCRA 应用后，插件默认读取受保护的：

```text
~/.config/tmcra/config.json
~/.config/tmcra/installation.json
```

只有文件位于其他位置时，才设置 `TMCRA_CONFIG_FILE` 和 `TMCRA_INSTALLATION_FILE`。托管 Gateway 应在服务进程环境中设置 `TMCRA_API_KEY` 和 `TMCRA_IDENTITY_SECRET`。不要把凭证放进插件 config、提示词或源码仓库。

以下环境变量可以覆盖非敏感配置：

```text
TMCRA_BASE_URL=https://api.tmcra.com
TMCRA_TENANT_ID=tenant-a
TMCRA_PROJECT_ID=shared-project-id
TMCRA_SCOPE_NAMESPACE=openclaw
TMCRA_GLOBAL_SCOPE=authorized-global-scope
TMCRA_PROJECT_SCOPE_PREFIX=authorized-project-prefix
TMCRA_QUEUE_PATH=/absolute/owner-only/pending-ingest.json
```

## 配置项目共享与信任权限

按照 [README.zh-CN.md](./README.zh-CN.md) 添加插件配置。完整自动链路必须同时启用：

```json5
hooks: {
  allowConversationAccess: true,
  allowPromptInjection: true
},
config: {
  sharedProjectId: "one-stable-id-for-the-team"
}
```

`allowPromptInjection` 允许回答前注入召回内容；`allowConversationAccess` 允许回答后读取完整轮次。关闭任意一项就会撤销对应能力。一个项目的所有 Agent 必须使用相同的 `sharedProjectId`，不要把 Agent ID 当成项目 ID。

config 还支持 `includeGlobalScope`、`requestTimeoutMs`、`maxContextChars`、`maxWindows`、`evidenceMode` 和 `drainIntervalMs`。`queuePath` 必须是绝对路径；当前生产证据合约使用 `maxWindows: 8`。

## 运行时全流程核验

修改安装包、配置、环境变量或 hook 权限后重启 Gateway：

```bash
openclaw gateway restart
openclaw plugins inspect tmcra-openclaw --runtime --json
openclaw plugins doctor
```

运行时检查必须指向 `dist/index.js`，并显示 `before_prompt_build`、`agent_end`、`gateway_start` 和 `gateway_stop`。随后在一次性项目中验证：

1. 用户提出问题后、模型执行前发生召回。
2. 最终回答成功后，恰好产生一条 user 和一条 assistant 记录。
3. 两个 Agent 使用同一个项目 ID 时项目 scope 相同、session 不同，Agent B 能召回 Agent A 的已完成进度。
4. 重放同一个结束事件时幂等键不变。
5. TMCRA 暂时不可用时 OpenClaw 仍然回答，轮次留在待写队列；服务恢复后自动续传且不重复写入。

`npm run test:server` 只能配合受限测试凭证和一次性 scope 使用。报告不会输出凭证或记忆正文。

本版本没有 Agent 私有召回开关；它处于明确关闭状态。
