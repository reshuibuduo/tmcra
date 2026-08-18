# 安装并核验 Python SDK

## 环境要求

- Python 3.10 或更高版本。
- 生产地址 `https://api.tmcra.com`。
- 只包含应用实际所需操作权限的 scope 凭证。

## 安装

安装官网当前分发的已验证包：

```bash
python -m pip install https://tmcra.com/downloads/integrations/tmcra_client-0.5.0-py3-none-any.whl
```

0.5.0 目前由 `tmcra.com` 直接分发，尚未发布到公共 PyPI。下面的本地构建命令仅用于源码检出目录。

凭证只能放在进程环境或密钥管理器中：

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
```

不要把根租户密钥打包进桌面端、浏览器或移动端代码。

## 从源码检出目录构建

```bash
python -m pip install build twine
python -m pytest -q
python -m build
python -m twine check dist/*
```

必须在源码目录之外的全新虚拟环境中安装 wheel：

```bash
python -m venv /tmp/tmcra-python-verify
/tmp/tmcra-python-verify/bin/python -m pip install \
  dist/tmcra_client-0.5.0-py3-none-any.whl
cd /tmp
/tmp/tmcra-python-verify/bin/python -c \
  "from tmcra_client import SyncClient, AsyncClient, SyncMemoryLifecycle; print('ok')"
```

Windows 使用 `Scripts/python.exe`，不是 `bin/python`。

## 核验可选自动生命周期

确定性测试必须证明顺序是“召回 -> Agent 回答 -> 写入”，用户和 Agent 消息主体分离，自动写入只进入项目 scope，多 Agent 共享项目 scope 但 session 独立，以及私有 scope 只召回不写入：

```bash
python -m pytest -q tests/test_lifecycle.py
```

连接真实服务时，只能使用一次性项目 scope 和短期受限 token。依次核验健康/就绪、种子写入完成、下一轮回答前召回、回答完成后才写入，以及 Agent B 能召回 Agent A 的项目进度。报告不得输出凭证或召回正文。

只有宿主明确建立了私有边界时才配置 `agent_private_scope`。它默认不设置，自动轮次始终只写入 `project_scope`。
