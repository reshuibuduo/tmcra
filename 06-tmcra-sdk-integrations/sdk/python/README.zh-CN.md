# tmcra-client Python SDK

0.5.0 同时提供同步/异步 TMCRA API 客户端和**可选**的 Agent 自动记忆生命周期。普通 endpoint client 仍然保持显式调用方式。

## 安装

```bash
python -m pip install https://tmcra.com/downloads/integrations/tmcra_client-0.5.0-py3-none-any.whl
```

0.5.0 目前由 `tmcra.com` 直接分发，尚未发布到公共 PyPI。在官网明确宣布 registry 版本前，不要把上述已验证地址改写成 `pip install tmcra-client`。

需要 Python 3.10+、`httpx` 和 Pydantic 2。本地发布包核验见 [INSTALL.zh-CN.md](./INSTALL.zh-CN.md)。

## 可选自动生命周期

```python
import os

from tmcra_client import AutomaticLifecycleConfig, SyncClient, SyncMemoryLifecycle


def call_agent(messages: list[dict[str, str]]) -> str:
    # 把 system/user 消息交给你的 Agent 模型。
    return "Agent 已完成的回答"


with SyncClient(os.environ["TMCRA_BASE_URL"], api_key=os.environ["TMCRA_API_KEY"]) as client:
    memory = SyncMemoryLifecycle(
        client,
        AutomaticLifecycleConfig(
            project_scope="project-acme-checkout",       # 项目内所有 Agent 共用
            global_scope="user-42-global",               # 可选，只召回
            agent_id="implementer",                       # 归属与会话主体
            agent_metadata={"agent_name": "Implementer"},
            # agent_private_scope="agent-implementer-private",  # 可选，只召回；默认关闭
        ),
    )

    result = memory.run_turn(
        "接着解析器方案继续做",
        lambda prepared: call_agent(prepared.model_messages()),
        session_id="implementer-conversation-7",
        idempotency_key="turn-7-stable-retry-key",
    )
```

执行顺序固定为：

1. 根据用户本轮提问，依次召回可选的用户全局 scope、必需的项目共享 scope，以及可选的当前 Agent 私有 scope。
2. 把召回内容放入“不可信数据”边界，再调用回答函数。
3. 得到非空回答后，把用户消息和 Agent 回答分成两条记录，只写入项目共享 scope。
4. 默认等待写入任务完成，再返回任务状态。

默认 `recall_fail_open=True`。某个 scope 召回失败时会记录到 `PreparedTurn.recall_errors`，但 Agent 仍可回答。回答函数异常或返回空文本时不会写入不完整轮次。回答后的写入错误会抛给宿主；SDK 本身不提供本地持久重试队列。

## 多 Agent 边界

- 同一项目的所有专业 Agent 使用同一个 `project_scope`，不要把 Agent 名称拼进 scope。
- 不同 Agent 对话使用不同 `session_id`。不传时每次调用都会新建 session，因此多轮宿主应主动传入稳定的会话 ID。
- `agent_private_scope` 默认关闭；启用时必须同时提供 `agent_id`。它只用于当前 Agent 召回，绝不会成为自动写入目标。
- 用户消息保留 `role=user`、`actor_role=user` 和 `target_agent_id`；Agent 回答保留 `role=assistant`、`actor_role=assistant`、`agent_id` 及自定义 Agent 元数据。
- 自动对话写入只进入项目 scope。稳定的用户基础信息应通过产品中的显式流程提升到全局 scope。

异步版本为 `AsyncMemoryLifecycle`，其回答回调可以是同步函数或异步函数。

## 显式 API 调用与测试

需要自行控制写入、召回、图谱、保留策略或导出时，直接使用 `SyncClient` / `AsyncClient`。生产召回使用固定 Top8 证据合约，`max_windows` 省略或设为 `8`。根租户密钥只能放在可信后端；非可信客户端必须使用短期、最小权限、限定 scope 的 token。

```bash
python -m pip install -e ".[test]"  # 仅用于源码检出目录的开发构建
python -m pytest -q
```

本地确定性测试使用 `httpx.MockTransport`，不连接真实服务，也不需要凭证。
