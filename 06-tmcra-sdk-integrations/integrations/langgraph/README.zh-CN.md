# LangGraph 的 TMCRA 记忆节点

该包提供 `recall_node -> model -> ingest_node` 的长期记忆生命周期，不替代 LangGraph checkpoint。每次调用必须提供稳定的 scope、session、turn 和时间戳；outbox 与 reconcile 用原始请求体和幂等键处理响应丢失。

只有 job `succeeded` 才能确认队列记录。详见仓库 `docs/integrations/langgraph.zh-CN.md`。
