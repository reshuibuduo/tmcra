"use client";

import { useEffect } from "react";
import Link from "next/link";
import { MarketingFooter, MarketingHeader } from "../../MarketingShell";
import { useLanguage } from "../../i18n";

const pythonExample = `from tmcra_client import (
    AutomaticLifecycleConfig,
    SyncClient,
    SyncMemoryLifecycle,
)

with SyncClient(BASE_URL, api_key=access_token) as client:
    memory = SyncMemoryLifecycle(client, AutomaticLifecycleConfig(
        project_scope="project_checkout",
        global_scope="user_global",
        agent_id="planner",
        agent_metadata={"specialty": "planning"},
        # agent_private_scope="agent_planner_private",  # opt in
    ))
    turn = memory.run_turn(
        user_text,
        lambda prepared: call_model(prepared.model_messages()),
        session_id=session_id,
    )`;

const typescriptExample = `import {
  TMCRAClient,
  TMCRAMemoryLifecycle,
} from "@tmcra/typescript";

const client = new TMCRAClient({ baseUrl, apiKey: accessToken });
const memory = new TMCRAMemoryLifecycle(client, {
  projectScope: "project_checkout",
  globalScope: "user_global",
  agentMetadata: { agent_id: "coder", specialty: "implementation" },
  // agentPrivateScope: "agent_coder_private", // opt in
});

const turn = await memory.runTurn(
  userText,
  (prepared) => callModel(prepared.modelMessages()),
  { sessionId },
);`;

const openClawConfig = `{
  plugins: {
    entries: {
      "tmcra-openclaw": {
        enabled: true,
        hooks: {
          allowConversationAccess: true,
          allowPromptInjection: true
        },
        config: {
          sharedProjectId: "checkout-service",
          includeGlobalScope: true
        }
      }
    }
  }
}`;

const hermesCommands = `python -m pip install https://tmcra.com/downloads/integrations/tmcra_hermes_plugin-0.3.0rc2-py3-none-any.whl
tmcra-hermes install
hermes memory setup
tmcra-hermes status`;

const mcpCommands = `python -m pip install https://tmcra.com/downloads/integrations/tmcra_mcp_server-0.3.0rc2-py3-none-any.whl

# Generic MCP: explicit tools
tmcra-mcp-setup install --mode explicit
tmcra-mcp-setup status --mode explicit

# Codex: optional automatic lifecycle Hooks
tmcra-mcp-setup install --mode codex-hooks
tmcra-mcp-setup status --mode codex-hooks`;

function CodePanel({ label, code }: { label: string; code: string }) {
  return (
    <div className="automatic-code-panel">
      <span>{label}</span>
      <pre><code>{code}</code></pre>
    </div>
  );
}

export default function AutomaticMemoryPage() {
  const { t } = useLanguage();

  useEffect(() => {
    document.title = t(
      "Automatic Agent memory integrations · TMCRA",
      "Agent 自动记忆接入 · TMCRA",
    );
  }, [t]);

  return (
    <main className="marketing-page automatic-memory-page">
      <MarketingHeader />

      <section className="branch-hero section-shell automatic-memory-hero">
        <div>
          <p className="eyebrow"><span /> {t("Automatic memory / 01", "自动记忆 / 01")}</p>
          <h1>{t(
            "Recall on the question. Write after the answer.",
            "用当前问题召回，在回答完成后写入。",
          )}</h1>
          <p>{t(
            "TMCRA cannot know what to recall before a user asks something. An automatic integration first uses the new prompt to recall and inject relevant evidence, lets the Agent answer with that context, and only then records the user message and final assistant response as two separate actors.",
            "用户还没有提出问题时，系统并不知道这一轮该召回什么。自动接入会先用新问题检索并注入相关记忆，再让 Agent 基于这些上下文回答；回答完成后，才把用户消息与助手最终回复作为两个不同主体分别写入。",
          )}</p>
          <div className="hero-actions">
            <a className="button button-primary" href="#lifecycle">{t("See the lifecycle", "查看完整链路")} <span aria-hidden="true">↓</span></a>
            <a className="button button-secondary" href="#integrations">{t("Choose an integration", "选择接入方式")} <span aria-hidden="true">↓</span></a>
          </div>
        </div>
        <aside className="branch-hero-aside automatic-memory-order">
          <span>{t("TURN ORDER", "单轮顺序")}</span>
          <strong>R·A·W</strong>
          <p>{t(
            "Recall → Answer → Write. Recall belongs to the current question; persistence belongs after the completed answer.",
            "召回 → 回答 → 写入。召回服务于当前问题，持久化发生在完整回答之后。",
          )}</p>
        </aside>
      </section>

      <nav className="automatic-jumpbar section-shell" aria-label={t("Integration navigation", "接入方式导航")}>
        <span>{t("GO TO", "快速定位")}</span>
        <a href="#openclaw">OpenClaw</a>
        <a href="#hermes">Hermes</a>
        <a href="#python">Python</a>
        <a href="#javascript-typescript">JavaScript / TypeScript</a>
        <a href="#mcp">MCP / Codex Hooks</a>
        <a href="#downloads">{t("Downloads", "安装包")}</a>
      </nav>

      <section className="automatic-section section-shell section-block" id="lifecycle">
        <div className="section-heading split-heading">
          <div>
            <p className="section-index">02 / {t("TURN LIFECYCLE", "单轮生命周期")}</p>
            <h2>{t("The current prompt starts the chain.", "链路从这一轮的新问题开始。")}</h2>
          </div>
          <p>{t(
            "Automatic means the host owns the timing. It does not mean TMCRA guesses a query in advance, nor that merely connecting an MCP server can observe every conversation.",
            "“自动”指宿主系统负责在正确时机触发生命周期，而不是让 TMCRA 提前猜测问题，也不代表仅连接一个 MCP Server 就能旁观所有对话。",
          )}</p>
        </div>

        <ol className="automatic-lifecycle">
          <li>
            <span>01</span>
            <div><em>{t("QUESTION", "提问")}</em><h3>{t("The user submits the new prompt", "用户提交这一轮问题")}</h3><p>{t("The prompt becomes the recall query. Nothing from the unfinished turn is written yet.", "这条问题成为召回查询；尚未完成的本轮内容不会提前写入。")}</p></div>
          </li>
          <li>
            <span>02</span>
            <div><em>{t("RECALL + ANSWER", "召回 + 回答")}</em><h3>{t("Evidence is recalled, fenced, and injected", "召回证据，经安全边界处理后注入")}</h3><p>{t("The host recalls allowed scopes, places bounded evidence in untrusted system context, then calls its existing model or Agent.", "宿主从获准的记忆范围召回内容，把有界证据作为不可信系统上下文注入，再调用原有模型或 Agent。")}</p></div>
          </li>
          <li>
            <span>03</span>
            <div><em>{t("WRITE", "写入")}</em><h3>{t("The completed turn is persisted", "完整轮次在回答后持久化")}</h3><p>{t("The user prompt and final assistant answer are submitted separately to shared project memory, with role, Agent, session, and source provenance preserved.", "用户问题与助手最终回复分别写入项目共享记忆，并保留 role、Agent、Session 与来源信息。")}</p></div>
          </li>
        </ol>

        <aside className="automatic-callout">
          <strong>{t("Failure boundary", "失败边界")}</strong>
          <p>{t(
            "Recall can fail open when configured, so an Agent turn can continue without memory. Post-answer writes use an asynchronous job or durable local queue depending on the adapter; retries must keep the same idempotency identity.",
            "如果配置为失败放行，召回不可用时 Agent 仍可继续回答。回答后的写入会根据适配器进入异步 Job 或本地持久队列；重试时必须保持同一幂等身份。",
          )}</p>
        </aside>
      </section>

      <section className="automatic-section section-shell section-block" id="multi-agent">
        <div className="section-heading split-heading">
          <div>
            <p className="section-index">03 / {t("MULTI-AGENT MEMORY", "多 Agent 记忆")}</p>
            <h2>{t("Share the project. Preserve the speaker.", "共享项目上下文，同时保留发言主体。")}</h2>
          </div>
          <p>{t(
            "Specialized Agents should not be split into unrelated project graphs. They share one project boundary, while each conversation keeps its own session and every record keeps its actor attribution.",
            "同一项目里的专业 Agent 不应被切成互不相关的项目图。它们共享同一个项目边界，但各自保留会话 Session，每条记录也保留明确的主体归属。",
          )}</p>
        </div>

        <div className="scope-stack" aria-label={t("Memory scope model", "记忆范围模型")}>
          <article className="scope-card is-global">
            <header><span>01</span><em>{t("RECALL", "召回")}</em></header>
            <h3>{t("User global", "用户全局记忆")}</h3>
            <p>{t("Stable user facts and preferences that may be useful across projects. Automatic turn writers do not place project chat here.", "可跨项目使用的稳定用户信息与偏好。自动轮次写入不会把项目对话塞进这一层。")}</p>
          </article>
          <article className="scope-card is-project">
            <header><span>02</span><em>{t("RECALL + WRITE", "召回 + 写入")}</em></header>
            <h3>{t("Shared project", "项目共享记忆")}</h3>
            <p>{t("The default collaboration boundary. Planner, coder, reviewer, and other Agents use the same project scope so each can recall the others’ progress.", "默认的协作边界。规划、编码、审查等不同 Agent 使用同一个项目 Scope，因此能够召回彼此的进度。")}</p>
          </article>
          <article className="scope-card is-private">
            <header><span>03</span><em>{t("OPTIONAL RECALL", "可选召回")}</em></header>
            <h3>{t("Current-Agent private", "当前 Agent 私有记忆")}</h3>
            <p>{t("Off by default and recall-only in the current Python and JavaScript/TypeScript lifecycle wrappers. Automatic writes still go to the shared project scope.", "默认关闭；当前 Python 与 JavaScript/TypeScript 生命周期封装只会从这一层召回。自动写入仍然落在项目共享 Scope。")}</p>
          </article>
        </div>

        <div className="provenance-contract">
          <div>
            <p className="section-index">{t("SCOPE IS NOT SESSION", "SCOPE 不等于 SESSION")}</p>
            <h3>{t("One project boundary, distinct Agent sessions.", "同一个项目边界，不同的 Agent 会话。")}</h3>
            <p>{t(
              "Use the same stable project key for every collaborating Agent. Keep different session IDs for separate conversations. Agent identity must not be included in the project-scope key, or cross-Agent recall will be broken.",
              "同一项目中的 Agent 应使用同一个稳定项目标识，不同对话则保留不同 Session ID。不要把 Agent 身份拼进项目 Scope 标识，否则会破坏跨 Agent 召回。",
            )}</p>
          </div>
          <CodePanel label="actor.provenance" code={`{
  "role": "user",
  "metadata": { "actor_role": "user", "target_agent_id": "planner" }
}
{
  "role": "assistant",
  "metadata": { "actor_role": "assistant", "agent_id": "planner" }
}`} />
        </div>

        <aside className="automatic-callout is-accent">
          <strong>{t("Why both sides are recallable", "为什么用户与 Agent 的内容都要召回")}</strong>
          <p>{t(
            "User records carry requirements, decisions, and facts. Assistant records carry completed work, current progress, and results. Later Agents need both, but TMCRA never promotes an assistant statement into something the user said.",
            "用户记录承载需求、决策与事实；助手记录承载已经完成的工作、当前进度与结果。后续 Agent 两者都需要，但 TMCRA 不会把助手的回答冒充成用户本人说过的话。",
          )}</p>
        </aside>
      </section>

      <section className="automatic-section section-shell section-block" id="integrations">
        <div className="section-heading split-heading">
          <div><p className="section-index">04 / {t("INTEGRATION PATHS", "接入路径")}</p><h2>{t("Choose the lifecycle your host can actually expose.", "按宿主真正提供的生命周期接入。")}</h2></div>
          <p>{t("Native adapters attach to host events. SDK wrappers surround your own model call. Generic MCP remains an explicit tool surface unless its host adds lifecycle Hooks.", "原生适配器连接宿主事件；SDK 封装包裹你自己的模型调用；普通 MCP 仍是显式工具层，除非宿主另行提供生命周期 Hook。")}</p>
        </div>
      </section>

      <section className="adapter-section section-shell" id="openclaw">
        <header className="adapter-heading">
          <div><span>01</span><p>NATIVE HOOKS · PILOT</p></div>
          <h2>OpenClaw</h2>
          <p>{t("Automatic recall and capture through the OpenClaw plugin lifecycle.", "通过 OpenClaw 插件生命周期完成自动召回与采集。")}</p>
        </header>
        <div className="adapter-body">
          <div className="adapter-facts">
            <article><code>before_prompt_build</code><p>{t("Uses the current prompt to recall global and shared-project evidence, then returns bounded prependSystemContext before model execution.", "使用当前问题召回用户全局与项目共享证据，并在模型执行前返回有界的 prependSystemContext。")}</p></article>
            <article><code>agent_end</code><p>{t("After a successful, non-aborted turn, queues the user prompt and final assistant answer as separate messages.", "成功且未中止的回答结束后，把用户问题和助手最终回复作为两条独立消息放入队列。")}</p></article>
            <article><code>gateway_start / gateway_stop</code><p>{t("Drains the owner-readable durable queue so a temporary API failure does not discard a completed turn.", "处理仅文件所有者可读的持久队列，避免临时 API 故障丢失已完成轮次。")}</p></article>
          </div>
          <div className="adapter-side">
            <CodePanel label="openclaw.config" code={openClawConfig} />
            <p>{t(
              "Give every Agent on the same workstream the same sharedProjectId. OpenClaw’s agentId is excluded from the project scope but included in the derived session and message attribution. If sharedProjectId is omitted, the adapter falls back to workspace, then chat identity.",
              "同一工作流中的所有 Agent 应使用相同的 sharedProjectId。OpenClaw 的 agentId 不参与项目 Scope 计算，但会进入派生 Session 与消息归属。未配置 sharedProjectId 时，适配器依次使用工作区或聊天身份。",
            )}</p>
            <p className="adapter-warning">{t("The operator must explicitly allow conversation access and prompt injection. Credentials stay in protected device configuration or Gateway environment, never in the model tool surface.", "管理员必须明确授权读取对话与注入 Prompt。凭据只能保存在受保护的设备配置或 Gateway 环境中，不会暴露为模型工具。")}</p>
          </div>
        </div>
      </section>

      <section className="adapter-section section-shell" id="hermes">
        <header className="adapter-heading">
          <div><span>02</span><p>MEMORY PROVIDER · PILOT</p></div>
          <h2>Hermes Agent</h2>
          <p>{t("Automatic memory through the current Hermes MemoryProvider contract.", "通过当前 Hermes MemoryProvider 合同接入自动记忆。")}</p>
        </header>
        <div className="adapter-body">
          <div className="adapter-facts">
            <article><code>prefetch / queue_prefetch</code><p>{t("Recalls the exact current query. The queued form warms an exact-query cache without changing recall semantics.", "召回当前的精确问题；队列形式只预热同一查询的缓存，不改变召回语义。")}</p></article>
            <article><code>sync_turn</code><p>{t("Queues the completed primary user/assistant turn. Primary Agents on the same project share a scope but retain distinct sessions.", "写入已完成的主对话轮次。同一项目的主 Agent 共享 Scope，但各自保留不同 Session。")}</p></article>
            <article><code>on_delegation</code><p>{t("Records the parent delegation request and delegated result as assistant-side work with distinct parent and child Agent attribution; neither is mislabeled as a user statement.", "把父 Agent 的委派请求和子 Agent 的执行结果都记录为助手侧工作，同时区分父、子 Agent；两者都不会被误标成用户陈述。")}</p></article>
          </div>
          <div className="adapter-side">
            <CodePanel label="hermes.setup" code={hermesCommands} />
            <p>{t(
              "Set one stable TMCRA_PROJECT_ID, project root, or workspace value across cooperating Agents. Hermes derives one shared project scope while agent_identity participates in each opaque session ID and actor provenance.",
              "协作 Agent 应使用同一个稳定的 TMCRA_PROJECT_ID、项目根目录或工作区值。Hermes 会据此派生共享项目 Scope，同时把 agent_identity 用于各自的不透明 Session ID 与主体来源。",
            )}</p>
            <p className="adapter-warning">{t("The provider is single-select and does not patch Hermes core. Normal device authorization can supply protected credentials; the durable pending queue contains conversation content and must remain owner-readable.", "该 Provider 以单选方式启用，不修改 Hermes 核心。普通设备授权可以提供受保护凭据；本地待写队列含有对话内容，必须限制为文件所有者可读。")}</p>
          </div>
        </div>
      </section>

      <section className="adapter-section section-shell" id="python">
        <header className="adapter-heading">
          <div><span>03</span><p>OPTIONAL WRAPPER · PREVIEW</p></div>
          <h2>Python SDK</h2>
          <p>{t("Wrap a synchronous or asynchronous model call without replacing your Agent runtime.", "在不替换 Agent Runtime 的前提下，包裹同步或异步模型调用。")}</p>
        </header>
        <div className="adapter-body">
          <div className="adapter-copy">
            <h3>{t("The wrapper owns the turn order", "由封装器控制单轮顺序")}</h3>
            <p>{t("SyncMemoryLifecycle and AsyncMemoryLifecycle call prepare_turn first, pass fenced context through PreparedTurn.model_messages(), then call commit_turn only after a non-empty assistant answer exists.", "SyncMemoryLifecycle 与 AsyncMemoryLifecycle 会先执行 prepare_turn，通过 PreparedTurn.model_messages() 交付安全边界内的上下文，并且只在拿到非空助手回复后执行 commit_turn。")}</p>
            <ul>
              <li>{t("Required project_scope is the shared write boundary.", "必填的 project_scope 是共享写入边界。")}</li>
              <li>{t("global_scope is recalled first when configured, but is never written automatically.", "配置 global_scope 后会优先召回，但不会自动写入。")}</li>
              <li>{t("agent_private_scope is optional, recall-only, and requires agent_id.", "agent_private_scope 可选且只用于召回；启用时必须同时提供 agent_id。")}</li>
              <li>{t("The default waits for the ingest Job and verifies success.", "默认等待写入 Job 结束并确认成功。")}</li>
            </ul>
          </div>
          <div className="adapter-side">
            <CodePanel label="python · tmcra-client" code={pythonExample} />
            <p className="adapter-command"><code>python -m pip install https://tmcra.com/downloads/integrations/tmcra_client-0.3.0rc2-py3-none-any.whl</code></p>
          </div>
        </div>
      </section>

      <section className="adapter-section section-shell" id="javascript-typescript">
        <header className="adapter-heading">
          <div><span>04</span><p>OPTIONAL WRAPPER · PREVIEW</p></div>
          <h2>JavaScript / TypeScript SDK</h2>
          <p>{t("The same compiled package serves JavaScript at runtime and TypeScript with declarations.", "同一个编译包既可在 JavaScript 中直接运行，也向 TypeScript 提供类型声明。")}</p>
        </header>
        <div className="adapter-body">
          <div className="adapter-copy">
            <h3>TMCRAMemoryLifecycle</h3>
            <p>{t("prepareTurn recalls the configured scopes in parallel and returns modelMessages(). commitTurn writes both actors to projectScope with read-your-writes consistency; runTurn connects both around your answer callback.", "prepareTurn 会并行召回已配置的范围，并返回 modelMessages()；commitTurn 把两个对话主体以 read-your-writes 一致性写入 projectScope；runTurn 则把两者连接在你的回答回调前后。")}</p>
            <ul>
              <li>{t("Use one projectScope for every Agent on the project.", "同一项目的所有 Agent 使用同一个 projectScope。")}</li>
              <li>{t("Put agent_id and specialization in agentMetadata; it attributes records without partitioning the project.", "在 agentMetadata 中提供 agent_id 与分工信息；它用于标记来源，不会切分项目。")}</li>
              <li>{t("agentPrivateScope is opt-in recall only; automatic writes remain shared.", "agentPrivateScope 仅在显式启用时参与召回；自动写入仍然共享。")}</li>
            </ul>
          </div>
          <div className="adapter-side">
            <CodePanel label="javascript / typescript · @tmcra/typescript" code={typescriptExample} />
            <p className="adapter-command"><code>npm install https://tmcra.com/downloads/integrations/tmcra-typescript-0.3.0-rc.2.tgz</code></p>
          </div>
        </div>
      </section>

      <section className="adapter-section section-shell" id="mcp">
        <header className="adapter-heading">
          <div><span>05</span><p>EXPLICIT MCP / OPTIONAL CODEX HOOKS · PREVIEW</p></div>
          <h2>MCP and Codex Hooks</h2>
          <p>{t("Choose explicit memory tools or a host lifecycle that can automate them.", "选择显式记忆工具，或使用能够自动触发生命周期的宿主 Hook。")}</p>
        </header>
        <div className="adapter-body">
          <div className="mcp-mode-grid">
            <article>
              <span>{t("DEFAULT", "默认")}</span>
              <h3>{t("Generic MCP is explicit", "普通 MCP 是显式调用")}</h3>
              <p>{t("The server exposes tmcra_recall, tmcra_ingest, tmcra_get_job, and tmcra_wait_job. The host must deliberately call recall before answering and ingest afterward. Connecting stdio alone cannot observe a host’s turns.", "Server 提供 tmcra_recall、tmcra_ingest、tmcra_get_job 与 tmcra_wait_job。宿主必须主动在回答前调用召回、回答后调用写入；仅连接 stdio 无法自动观察宿主对话。")}</p>
            </article>
            <article>
              <span>{t("OPTIONAL", "可选")}</span>
              <h3>{t("Codex Hooks automate the lifecycle", "Codex Hooks 自动触发生命周期")}</h3>
              <p>{t("The codex-hooks setup delegates to the existing TMCRA Codex plugin. UserPromptSubmit recalls and injects memory; Stop captures separate user and assistant records. The user must restart Codex, inspect /hooks, and grant trust.", "codex-hooks 模式复用现有 TMCRA Codex 插件。UserPromptSubmit 负责召回与注入，Stop 负责分别采集用户与助手记录。用户仍需重启 Codex、检查 /hooks 并主动授予信任。")}</p>
            </article>
          </div>
          <div className="adapter-side">
            <CodePanel label="tmcra-mcp-setup" code={mcpCommands} />
            <p>{t("For a multi-Agent MCP host, pass the same shared scope, distinct session_id values, preserved message roles, and an optional real agent_id. Generic MCP does not invent an Agent identity when the host provides none.", "多 Agent MCP 宿主应传入相同的共享 Scope、不同的 session_id、原样保留的消息 role，以及可选但真实的 agent_id。宿主没有提供身份时，普通 MCP 不会凭空编造 Agent。")}</p>
            <Link className="adapter-link" href="/developers/codex">{t("Open the full Codex installation guide", "查看完整 Codex 安装指南")} <span aria-hidden="true">→</span></Link>
          </div>
        </div>
      </section>

      <section className="adapter-section section-shell" id="downloads">
        <header className="adapter-heading">
          <div><span>06</span><p>VERIFIED ARTIFACTS</p></div>
          <h2>{t("Download the tested packages", "下载已验收的接入包")}</h2>
          <p>{t("These files are the same artifacts used for clean-install and package validation. Verify SHA-256 before installation.", "这些文件与干净环境安装和打包验收使用的是同一批制品；安装前请核对 SHA-256。")}</p>
        </header>
        <div className="automatic-download-grid">
          <a href="/downloads/integrations/tmcra-openclaw-memory-0.3.0-rc.2.tgz"><b>OpenClaw</b><span>0.3.0-rc.2 / npm tgz</span></a>
          <a href="/downloads/integrations/tmcra_hermes_plugin-0.3.0rc2-py3-none-any.whl"><b>Hermes</b><span>0.3.0rc2 / Python wheel</span></a>
          <a href="/downloads/integrations/tmcra_client-0.3.0rc2-py3-none-any.whl"><b>Python SDK</b><span>0.3.0rc2 / wheel</span></a>
          <a href="/downloads/integrations/tmcra-typescript-0.3.0-rc.2.tgz"><b>JavaScript / TypeScript</b><span>0.3.0-rc.2 / npm tgz</span></a>
          <a href="/downloads/integrations/tmcra_mcp_server-0.3.0rc2-py3-none-any.whl"><b>MCP Server</b><span>0.3.0rc2 / Python wheel</span></a>
          <a href="/downloads/integrations/SHA256SUMS.txt"><b>SHA-256</b><span>{t("Checksum manifest", "校验清单")}</span></a>
        </div>
        <p className="automatic-download-note">{t("Source distributions and the machine-readable manifest remain in the same directory for reproducible deployment.", "源码分发包和机器可读 manifest 也保存在同一目录，便于复现部署。")}</p>
      </section>

      <section className="automatic-checklist section-shell section-block">
        <div>
          <p className="section-index">05 / {t("ACCEPTANCE CHECK", "验收检查")}</p>
          <h2>{t("Verify behavior, not just installation.", "验收实际行为，而不只是“已安装”。")}</h2>
        </div>
        <ol>
          <li><span>01</span><p>{t("Seed a unique fact through Agent A and wait for its write to complete.", "让 Agent A 写入一个唯一事实，并等待写入完成。")}</p></li>
          <li><span>02</span><p>{t("Ask a related new question through Agent B in the same project and verify recall occurs before its model call.", "在同一项目中通过 Agent B 提出相关的新问题，并确认召回发生在模型调用之前。")}</p></li>
          <li><span>03</span><p>{t("Inspect evidence: Agent B should see Agent A’s progress, while role, agent_id, session, and source remain distinguishable.", "检查证据：Agent B 应能看到 Agent A 的进度，同时 role、agent_id、Session 与来源仍可区分。")}</p></li>
          <li><span>04</span><p>{t("Verify an Agent-private scope is invisible unless that optional recall scope is explicitly configured.", "确认 Agent 私有 Scope 只有在显式配置为召回范围后才可见。")}</p></li>
          <li><span>05</span><p>{t("Interrupt the API briefly and confirm the adapter’s Job or durable queue recovers without duplicating the turn.", "短暂中断 API，确认适配器能够通过 Job 或持久队列恢复，且不会重复写入同一轮次。")}</p></li>
        </ol>
      </section>

      <section className="branch-cta">
        <div className="section-shell">
          <div><p className="section-index">06 / {t("ACCESS", "接入")}</p><h2>{t("Connect one real workflow end to end.", "先把一条真实工作流完整跑通。")}</h2><p>{t("Tell us the host, number of Agents, project-boundary rule, and expected traffic. We will review the integration path and scoped authorization requirements.", "提交宿主平台、Agent 数量、项目边界规则和预期流量，我们会据此评估接入路径与最小授权范围。")}</p></div>
          <a className="button button-primary" href="/access">{t("Request integration access", "申请接入试用")} <span aria-hidden="true">→</span></a>
        </div>
      </section>

      <MarketingFooter />
    </main>
  );
}
