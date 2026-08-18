# 安装 OpenAI Agents SDK 的 TMCRA 适配

```bash
python -m pip install tmcra-openai-agents
```

在服务端创建 `AsyncMemoryClient` 和限定 scope 的 token，为每个并发会话
创建一个 `TMCRAAgentsMemory`，再把它的输入回调和 `RunHooks` 注册到 Agents
SDK。可能重启或发生响应丢失时，应把 outbox 放在持久化目录。

本地 contract 可用 `python -m pytest -q` 验证。该包不会自动修改宿主配置，
本地测试也不等于真实 Agents Runner E2E。
