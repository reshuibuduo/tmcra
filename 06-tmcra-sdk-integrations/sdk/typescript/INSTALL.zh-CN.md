# 安装并核验 TypeScript/JavaScript SDK

## 环境要求

- Node.js 18.17 或更高版本，或其他支持 `fetch` 的 ESM 运行时。
- 生产地址 `https://api.tmcra.com`。
- 只包含应用实际所需操作权限的 scope 凭证。

## 安装

```bash
npm install https://tmcra.com/downloads/integrations/tmcra-typescript-0.5.0.tgz
```

0.5.0 目前由 `tmcra.com` 直接分发，尚未发布到公共 npm registry。下面的本地构建命令仅用于源码检出目录。

JavaScript 和 TypeScript 使用同一个包。凭证只能放在服务端环境或密钥管理器中：

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
```

不要把根租户凭证打包进浏览器、桌面端或移动端程序。

## 从源码检出目录构建

```bash
npm ci
npm test
npm run typecheck
npm pack --pack-destination artifacts
```

必须在源码目录之外创建全新项目并安装 tarball：

```bash
mkdir /tmp/tmcra-typescript-verify
cd /tmp/tmcra-typescript-verify
npm init -y
npm install /absolute/path/tmcra-typescript-0.5.0.tgz
node --input-type=module -e \
  "import { TMCRAClient, TMCRAMemoryLifecycle } from '@tmcra/typescript'; console.log(typeof TMCRAClient, typeof TMCRAMemoryLifecycle)"
```

预期输出是 `function function`，证明 Node 加载的是实际发布包，而不是仓库源码。

## 核验可选自动生命周期

确定性测试必须证明顺序是“召回 -> Agent 回答 -> 写入”，用户和 Agent 消息主体分离，自动写入只进入项目 scope，多 Agent 共享项目 scope 但 session 独立，以及私有 scope 只召回不写入：

```bash
node --experimental-strip-types --test test/lifecycle.test.ts
```

连接真实服务时，只能使用一次性项目 scope 和短期受限 token。依次核验种子写入完成、下一轮回答前召回、回答完成后才写入，以及 Agent B 能召回 Agent A 的项目进度。报告不得输出凭证或召回正文。

`agentPrivateScope` 默认不配置。配置后也只是召回来源，自动轮次始终只写入 `projectScope`。
