# TMCRA Benchmarks — 可复现评测与成绩溯源

[English](README.md)

本组件公开 TMCRA V4 的完整评测侧实现：

- LongMemEval 构建、召回、证据编译、回答、裁判、审计和分阶段质量门；
- 语义证据规划与零 API 回归门；
- 修复、迁移、恢复、成本统计及慢速图谱覆盖工具；
- `algorithm/` 下与生产链路一致的固定算法副本；
- 本地已验证的 581 项发布测试。

已验证的默认生产配置使用 `Qwen3.6-35B-A3B` 执行生成与规划，BGE-M3
负责向量嵌入，BGE reranker V2 M3 负责 Cross Encoder 重排。模型别名和端点均可配置，
代码不会按精确型号拦截其他本地模型或 OpenAI-compatible 模型。替换模型后可能需要自行
调整 Prompt、上下文窗口和质量阈值，调整后的成绩需单独记录。

## 成绩来源边界

- LongMemEval-S 冻结评分为 **411/500（82.2%）**，来自一次完整的 500 题端到端运行。
- 新版主线、cleanroom、no-GNN 等测量必须单独报告，不与 411/500 合并。
- `TMCRA_V4_SEMANTIC100_BENCHMARK_REPORT.md` 记录公开的 100 题对比及精确回归题号。
- LoCoMo 的统计口径独立，详见 `LOCOMO_BENCHMARK_REPORT.md`。

仓库不捆绑上游评测数据集和第三方裁判响应。请按上游许可自行取得数据，并在运行产物中
保留质量门计划、输入哈希、裁判输出和报告。`RELEASE_MANIFEST.json` 固定的是本组件源码
快照，不代表仓库分发第三方数据或模型权重。

## 环境与路径

已验证解释器为 Python 3.11。在仓库根目录安装 Memory API 的同一组运行依赖：

```bash
python -m pip install -r 02-tmcra-memory-api/requirements-tmcra-service.txt
```

部分脚本保留原开发机路径作为默认值，数据目录、仓库目录、模型路径和服务端点均可通过
环境变量或命令行参数覆盖。修改 `algorithm/` 中的固定算法后，需要同步组件 01/02、重建
共享核心清单并重新运行发布测试。

## 复现入口

```bash
cd 09-tmcra-benchmarks
export PYTHONPATH="$PWD/algorithm:$PWD/../02-tmcra-memory-api"

python run_tmcra_v4_build.py --help
python run_tmcra_v4_retrieve.py --help
python run_tmcra_v4_compile_semantic_evidence.py --help
python run_tmcra_v4_gpt54_answers.py --help
python run_tmcra_v4_evaluate.py --help

python plan_tmcra_v4_quality_gates.py --help
python score_tmcra_v4_quality_gate.py --help
python tmcra_v4_regression_gate.py --help
```

各命令通过 `--help` 给出数据集、运行目录、模型、端点和输出参数。运行目录会保留完成标记、
链路审计、题号顺序、来源 Top24、最终证据窗口、成本及评测报告，可从成绩追溯到来源会话与
不可变证据记录。

## 发布校验

Linux/macOS：

```bash
export PYTHONPATH="$PWD/algorithm:$PWD/../02-tmcra-memory-api"
python -m unittest discover -s . -p 'test*.py'
python generate_release_manifest.py --check
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="$(Resolve-Path algorithm);$(Resolve-Path ../02-tmcra-memory-api)"
python -m unittest discover -s . -p 'test*.py'
python generate_release_manifest.py --check
```

早期 V3 与旧 Memory API 契约测试保存在 `legacy_contract_tests/`，用于设计溯源，不参与
当前测试发现。Memory API 的现行发布校验由组件 02 负责。
