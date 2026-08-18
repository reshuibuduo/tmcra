# TMCRA 手机端语音模型选型（内部评估版）

更新日期：2026-08-16

## 结论

第一轮真机验证采用以下组合：

1. ASR：`sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01`
2. 声纹：`iic/speech_eres2netv2_sv_zh-cn_16k-common@v1.0.1`
3. 推理运行时：`sherpa-onnx 1.13.4`

这组配置优先验证中文、实时性、包体和持续运行功耗。当前 APK 仅打
`arm64-v8a`，减少无关 ABI 带来的包体膨胀。

## ASR 判断

### 当前内部评估模型

2025-04 的 small CTC Zipformer 是单文件 INT8 ONNX：

- 模型文件：26,342,340 bytes
- Token 文件：13,366 bytes
- SHA-256（模型）：`68c9c943840f7d9cf3e8a4970ba50f404feb5277f611fa82b7e72267786fa84a`
- SHA-256（Token）：`6fed8c6c248516f38e7faa19404b57413e8ce259f1cbc1fa4aebc86eac32fdfd`
- 语言：中文优先
- 工作方式：录音帧连续送入 OnlineRecognizer；界面可收到局部文本；VAD
  结束后定稿。

它适合先做手机端性能与准确率实验。模型仓库当前没有明确填写权重许可，
所以只能进入内部测试包，暂不进入正式商业发行包。

### 商用许可明确的后备模型

`sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23` 的模型卡标注
Apache-2.0，INT8 encoder、decoder、joiner 合计约 25 MB。它的发布日期更
早，结构也需要三个 ONNX 文件。若 2025 权重无法取得明确授权，就对两者做
同一台手机、同一批录音的 A/B，达到门槛后切换到 14M 版本。

### 暂不选用

- X-ASR zh-en：中英混合能力和许可证更清楚，约 160M 参数，第一轮常驻
  手机验证过重。
- Fun-ASR Nano：能力强，模型与运行栈仍明显大于本轮常驻音频 MVP 的目标。
- Whisper：模型和生态成熟，流式体验依赖分块策略；中文持续低功耗场景下，
  先验证原生 streaming Zipformer 更合理。
- Android 系统 SpeechRecognizer：实现依赖 OEM，是否离线、模型版本、可用性
  和行为都无法由 TMCRA 固定，不作为产品主链路。

## 声纹判断

ERes2NetV2 来自 3D-Speaker 体系。公开基准中它有 17.8M 参数，CNCeleb EER
为 6.14%；CAM++ 为 7.2M 参数、CNCeleb EER 为 6.78%。ERes2NetV2 更大，
但仍能作为手机端单段推理模型进入第一轮验证。

模型文件：

- 71,441,526 bytes
- SHA-256：`bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239`

声纹匹配采取保守策略：

- 已知说话人阈值：0.68
- 未知聚类复用阈值：0.72
- 第一名与第二名最小差：0.04
- 模板自适应阈值：0.82
- 少于 1.5 秒的片段不建声纹

这些值只是初始工程阈值。真机实验必须按环境、麦克风、人与人相似度重新
标定，重点报告 FAR、FRR、unknown 保留率和声纹混淆矩阵。

## 真机验收数据

至少在一台中端和一台高端 arm64 Android 手机上收集：

- 首次模型加载时间
- 连续流式 RTF、局部文本首字延迟、VAD 后定稿延迟
- P50/P95 CPU、峰值 RSS、每小时电量、温升
- 安静、街道、车内、多人近场、蓝牙耳机五类环境的 CER
- 本人/熟人/陌生人三类声纹的 FAR、FRR 和未知保留率
- 2、4、8 小时持续运行后的队列积压与崩溃情况

没有这些数据前，只能称为可安装工程包，不能称为手机端效果已验证。

## 上游资料

- sherpa-onnx Android 流式识别：<https://k2-fsa.github.io/sherpa/onnx/android/apk-cn.html>
- 2025 small CTC 模型：<https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01>
- Apache-2.0 14M 后备模型：<https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23>
- 3D-Speaker 与公开基准：<https://github.com/modelscope/3D-Speaker>
