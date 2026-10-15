# 四功能怎么调用

推荐入口：**Skill 理解意图 → `tmcra_workflows` MCP → 现有 HTTP API**。四个功能不需要四套工具。Skill 可发现、MCP 启动、接口接受和内容已索引是不同状态。

| 你要做什么 | 对 Agent 说 | 实际调用 |
| --- | --- | --- |
| 接续项目 | `$tmcra-workflows resume`，检查当前项目的下一步与旧失败 | `tmcra_workflows {session_id,project_path,operation:"resume"}` |
| 看决策变化 | `$tmcra-workflows timeline`，比较方案为什么改变 | `operation:"timeline"`，需要时加 `as_of` |
| 查看提醒 | `$tmcra-workflows remind` | `operation:"reminders"` 只读；真实提问/切换时用 `execute` + `reminder.evaluate` |
| 选择性迁移 | `$tmcra-workflows bridge`，只预览我选择的来源 | snapshot 列来源 → export 确认 → 目标 preview → 第二次确认 commit |

`session_id/project_path` 来自当前宿主。MCP `execute` 必须有 `action/payload/operation_id`，插件/local 版本没有可任意指定的 tenant/scope 字段。完整平台 `08-tmcra-mcp-server` 通用服务另用授权 `scope`，不使用 `project_path`；不要把两种适配器参数混用。相同重试使用同一 operation ID；已接受不等于已索引，导入仍 `external_unverified`，普通“记住了/测试通过”不构成机器验证。记忆包撤销、导出、提交、可能写入的计划恢复、失败任务重试、删除均保留具体 host 确认；用户取消或宿主不支持确认时不绕去 CLI/API。

[实际 MCP / HTTP 四功能请求与错误处理](WORKFLOW_MCP_API.zh-CN.md) · [证据、回执与完整边界](WORKFLOW_CENTER.zh-CN.md)

## 完整平台 `tmcra`

插件根：`07-tmcra-codex-plugins/tmcra-memory`。现有 Codex manifest 已指向 `./skills/`，加载插件后选 `$tmcra-workflows`。Claude Code 的现有插件名为 `tmcra-memory`，同一 skill 对应 **`/tmcra-memory:tmcra-workflows resume`**（另外可传 timeline / remind / bridge）。这是已打包入口语法，不表示你的当前客户端已经安装、启用或收到此版本。

在插件根执行真实只读 CLI：

```sh
node scripts/workflow_cli.mjs resume --project-path /actual/project --session-id actual-session
node scripts/workflow_cli.mjs timeline --project-path /actual/project --as-of 2026-10-16T08:00:00Z
node scripts/workflow_cli.mjs reminders --project-path /actual/project
```

普通显式写操作：把上文 API 示例保存为 `request.json`，执行 `node scripts/workflow_cli.mjs request --project-path /actual/project --session-id actual-session --input request.json`。敏感操作优先 MCP；CLI 需要真实 TTY 逐次交互，`--confirm` 不是自动同意。导入 commit 还需同 MCP 进程保存的真实 preview，单独 CLI token 不替代它。

MCP 身份来自已有插件配置，不输入租户；路径/项目 ID 解析到实际项目 scope。网页默认真实 global，通过服务端目录选择项目。HTTP 直连接口为 `/v1/scopes/{authorized_scope}/workflows`；导出需要 administrative key + `memory:export`，撤销还需删除权限。scope token 的正常401/403不应换钥匙绕过。

Claude Code 插件技能名称规则依据[官方 Skills 文档](https://code.claude.com/docs/en/skills#where-skills-live)；可用[官方 plugin-dir 方式](https://code.claude.com/docs/en/plugins)测试已构建插件。磁盘文件检查不是当前客户端加载验收。
