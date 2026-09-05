# TMCRA MCP Server

TMCRA MCP Server 让支持 MCP 的 Agent 工具显式调用长期记忆。它可以召回项目证据、按真实说话主体写入对话、保留多 Agent 的项目归属，并追踪异步写入直到终态。

[English](README.md)

## 功能

- **跨会话延续项目。** 在稳定的项目 scope 中召回长度受限、可直接注入的证据。
- **跨软件协作。** 不同 MCP 工具共享同一个项目 scope，同时保留各自的 session 来源。
- **区分说话主体与 Agent。** 用户、assistant、system、tool 分开写入；已知的 Agent 生产者或接收者会被保留。
- **显式回合生命周期。** 回答前准备召回，回答后写入本轮真实的用户消息和 Agent 回答。
- **写入失败可恢复。** 网络状态不确定时进入本地 SQLite 队列，使用同一幂等键继续提交。
- **结果可核验。** 召回、写入和任务状态必须通过结构化 receipt 校验。
- **九个真实 MCP 工具。** 召回、写入、准备回合、提交回合、恢复队列、查询任务、等待任务、会话记忆控制与定向反馈均有实现和测试。

`tmcra_memory_control` 提供 `normal`、`recall_only`、`off` 模式、任务续接和召回预算。控制与召回使用相同的准确 `session_id`；模式切换会阻止旧代待发送记录重放。

`tmcra_feedback` 展示原始证据、修改内容和作用范围，并通过 MCP 交互询问用户。明确同意后才提交；拒绝、取消、超时或宿主不支持询问时保持原记忆。有效纠错需要配套 Memory API 更新，并检查 `effective` 与 `correction_index_status`。可视化工作台由 Codex / DSH 发行版提供；本 MCP 包自动发现共享的本地安装。明确本地身份只允许数字回环地址的 HTTP；托管连接保持 HTTPS。

普通 MCP 客户端决定何时调用工具。仅连接 MCP Server 不会自动观察回答前后的生命周期。Codex 需要自动召回与写回时，请安装独立的 [TMCRA Codex Memory 插件](https://github.com/reshuibuduo/tmcra-plugin-codex)。

## 安装

### MCPB 安装包

从 [v1.0.0-rc.1 Release](https://github.com/reshuibuduo/tmcra-mcp-server/releases/tag/v1.0.0-rc.1) 下载 `tmcra-mcp-server-1.0.0-rc.1.mcpb`，在支持 MCPB 的客户端中打开。安装包使用跨平台 `uv` 运行时。托管模式在敏感字段中填写 API Key；Windows 完全本地模式先解压[独立运行包](https://github.com/reshuibuduo/tmcra/releases/tag/v1.0.0-rc.1)，双击 `Install-Local.cmd`，再重启 MCP 宿主。本地身份会自动发现并覆盖表单的云端地址与密钥，本地模式的 API Key 字段可留空。显式高级配置 `TMCRA_CONFIG_FILE` 仍优先生效。本地模型完整验收限制见运行包说明。

### Python wheel

```bash
python -m pip install \
  https://github.com/reshuibuduo/tmcra-mcp-server/releases/download/v1.0.0-rc.1/tmcra_mcp_server-1.0.0rc1-py3-none-any.whl
```

### 使用 `uvx` 直接运行 GitHub 版本

```bash
uvx --from "git+https://github.com/reshuibuduo/tmcra-mcp-server@v1.0.0-rc.1" tmcra-mcp
```

## 授权

注册 TMCRA 账户并创建 API Key，然后通过 MCP 客户端的密钥存储或 `TMCRA_API_KEY` 环境变量提供。不要把凭据提交到代码仓库。

```text
TMCRA_API_KEY=<你的 TMCRA API Key>
TMCRA_BASE_URL=https://api.tmcra.com
TMCRA_DEFAULT_SCOPE=<稳定的项目 scope，可选>
TMCRA_AGENT_ID=<已知的 Agent 身份，可选>
```

已在 TMCRA 应用登录的用户，也可以使用 `~/.config/tmcra/config.json` 设备配置。开发与自托管场景可用环境变量覆盖该文件。

API 地址必须是 HTTPS origin，不能包含内嵌凭据、query 或 fragment。

## 接入 MCP 客户端

安装 wheel 后，MCP 客户端可以启动：

```json
{
  "mcpServers": {
    "tmcra-memory": {
      "command": "tmcra-mcp",
      "env": {
        "TMCRA_API_KEY": "<由客户端安全保存>",
        "TMCRA_DEFAULT_SCOPE": "project-example"
      }
    }
  }
}
```

Codex 也可以使用安装助手：

```bash
tmcra-mcp-setup install --mode explicit
tmcra-mcp-setup status --mode explicit
```

## 工具

| 工具 | 作用 |
| --- | --- |
| `tmcra_recall` | 根据本轮问题返回最多八个可注入证据窗口。 |
| `tmcra_ingest` | 写入已经发生的消息，保留角色和 Agent 归属。 |
| `tmcra_turn_prepare` | 回答前召回，并在本地绑定真实用户消息。 |
| `tmcra_turn_commit` | 把已准备的用户消息和准确的 Agent 回答分开写入。 |
| `tmcra_reconcile` | 使用同一幂等键重试本地待处理记录。 |
| `tmcra_get_job` | 查询一次异步写入任务状态。 |
| `tmcra_wait_job` | 等待任务成功、失败或取消。 |

同一项目的协作 Agent 应使用同一个项目 `scope`。每段对话保留自己的 `session_id`。Agent ID 只负责归属，不会切开项目记忆。

召回结果带有 `trust_boundary: untrusted_memory_data`。客户端应把它当作证据，不能当作可执行指令。

## 显式回合流程

需要逐轮记忆的 MCP 客户端应当：

1. 收到本轮问题后调用 `tmcra_turn_prepare`。
2. 只把返回的 `injectable_context` 作为不可信证据注入。
3. 生成完整回答。
4. 使用同一个 `turn_id` 和准确的最终回答调用 `tmcra_turn_commit`。
5. 准确显示等待中或终态写入结果。

也可以使用底层流程：`tmcra_recall`、回答、`tmcra_ingest`，再调用 `tmcra_get_job` 或 `tmcra_wait_job`。

## 安全边界

- 凭据只从环境变量或受保护的 TMCRA 设备文件读取，服务不会打印凭据。
- API 地址必须使用 HTTPS。
- 召回、写入和任务响应必须通过结构化 receipt 校验。
- 召回记忆始终是不可信数据。
- 仓库只包含 MCP 客户端接入代码，不含生产服务源码或生产凭据。
- 当前工具集不提供删除与导出操作。

漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 开发验证

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
python -m twine check dist/*
```

测试覆盖 receipt 校验、scope 与角色归属、本地队列恢复、配置安全、安装逻辑，以及真实 MCP initialize/list/call 交换。

## 许可

Apache-2.0。详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
