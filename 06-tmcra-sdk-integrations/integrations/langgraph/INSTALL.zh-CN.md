# 安装 LangGraph 的 TMCRA 适配

```bash
python -m pip install tmcra-langgraph
```

在模型节点前加入 `recall_node`，在完成模型回合后加入 `ingest_node`。每次
调用提供稳定的 scope、session、turn 和时间戳；可能重启时启用持久 outbox，
并在 worker tick 中调用 `reconcile_pending()`。该包不替代 LangGraph checkpoint。

本地检查使用 `python -m pytest -q`；真实 checkpointer 和服务 E2E 必须使用一次性
scope 单独验收。
