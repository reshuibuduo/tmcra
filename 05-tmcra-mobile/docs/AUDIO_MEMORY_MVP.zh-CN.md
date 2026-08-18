# TMCRA 音频记忆 MVP 数据与隐私边界

## 目标

手机持续捕获有意义的语音片段，区分说话主体，把文字写入 TMCRA，并在出现
明确时间或承诺信息时生成本地提醒。静音、声纹向量和普通路径下的原始音频
不进入远端记忆检索。

## 一次语音事件

1. `AudioRecord` 以 16 kHz、单声道、16-bit PCM 采集。
2. 自适应 VAD 使用环境噪声底线切分有效片段，并保留 260 ms 前滚音频。
3. 同一录音帧实时送入手机端 Zipformer，产生局部转写。
4. VAD 检测到约 700 ms 停顿后，ASR 定稿。
5. 完整片段送入 ERes2NetV2，得到本地 embedding 并保守匹配说话人。
6. 本地数据库保存文本、角色、模型来源、同步状态和短期 WAV 路径。
7. 手机向服务器发出文字事件。服务器并行召回用户全局 Scope 和当前音频项目
   Scope，然后把当前事件写入项目 Scope。
8. 本人语音使用 `user` 角色；其他人使用带明确观察来源的 `tool` 角色。
9. 用户后续把 `spk_local_*` 标记为本人或熟人时，只同步 ID、标签、关系和
   revision。旧文字事件可以通过同一个 ID 解释身份。

Session 只是音频项目里的按日分组 ID，不是第三个召回 Scope。

## 远端允许字段

- transcript
- capture timestamp / duration / language
- opaque local speaker ID
- user-confirmed label and relation
- speaker match confidence
- ASR mode / model / confidence
- commitment / temporal / person hints
- client platform and version

API 合同会拒绝未知字段，因此 `audio`、`pcm`、`embedding`、`voiceprint` 等字段
无法混入正常写入请求。

## 本地保留

- WAV：最长 24 小时或 256 MiB，先到任一限制就从最旧文件开始删除。
- 声纹模板：Android Keystore 加密后存 SQLite；卸载应用或清除本地记忆会失去
  本地映射能力。
- 会话 Cookie：由独立安全存储保护。
- 未上传文字：进入 SQLite outbox，在服务重新启动后重试。
- 说话人标签：本地立即生效；远端同步失败时保留 pending/error 状态，下次登录
  或再次命名时重试。

## 远端兜底 ASR

默认关闭。用户在应用内单独确认后，仅当本地模型不可用或无法产生结果时，
当前片段才会发到转写接口。远端转写结果仍只以文字进入 TMCRA。后续版本需要
增加按次可见提示、费用统计和服务器侧音频立即删除回执。

## 尚未完成的产品能力

- 真机功耗、温升、蓝牙链路和嘈杂环境基准
- 模型按设备性能自动分档
- 重叠说话检测与双人同时发言分离
- 用户可见的声纹库管理页、合并/拆分 cluster、删除单个声纹
- 远端已写入记忆的同页删除闭环
- 视觉环境判断和“现在是否适合提醒”的多模态策略

