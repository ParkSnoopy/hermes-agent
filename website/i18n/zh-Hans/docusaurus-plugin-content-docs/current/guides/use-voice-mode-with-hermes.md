---
sidebar_position: 8
title: "在 Hermes 中使用语音模式"
description: "在 CLI 中设置和使用 Hermes 语音模式的实用指南"
---

# 在 Hermes 中使用语音模式

本页的 Python 依赖命令使用 [PM 准备的源码环境](../reference/package-management.md#developer-workflow)。
依赖变更后，请重新激活该 checkout 并重启 Hermes。

本指南是[语音模式功能参考](../user-guide/features/voice-mode.md)的实用配套文档。

功能页面介绍语音模式能做什么，本指南则说明如何真正用好它。

## 语音模式适合哪些场景

语音模式在以下情况特别有用：
- 需要免手持的 CLI 工作流

- 边走动边快速记录想法、调试问题或来回交流，而不是打字

## 选择你的语音模式方案

CLI 提供交互式麦克风和语音播放。

| 模式 | 最适合 | 平台 |
|---|---|---|
| 交互式麦克风循环 | 编码或研究时的个人免手持使用 | CLI |


推荐路径：
1. 先让文本模式正常工作
2. 再启用语音回复


## 第一步：确保普通 Hermes 先正常运行

在接触语音模式之前，请确认：
- Hermes 能正常启动
- 已配置好 provider（提供商）
- Agent 能正常回答文本 prompt（提示词）

```bash
hermes
```

问一个简单的问题：

```text
What tools do you have available?
```

如果文本模式还不稳定，请先修复它。

## 第二步：安装所需的额外依赖

### CLI 麦克风 + 播放

```bash
cd ~/.hermes/hermes-agent && python -c "import pm; pm.sync_venv(['voice'], explicit=True)"
```


### 高级 ElevenLabs TTS

```bash
cd ~/.hermes/hermes-agent && python -c "import pm; pm.sync_venv(['tts-premium'], explicit=True)"
```

### 本地 NeuTTS（可选）

声明的 `neutts` 依赖要求 Python 低于 3.14，而 Hermes 运行时要求 Python 3.14。
因此该 extra 不会在 Hermes 运行时安装 NeuTTS。请选择兼容的提供商。
独立的 NeuTTS 命令提供商需要自行管理兼容的 Python 环境。

`all` extra 并不包含所有可选语音依赖。

## 第三步：安装系统依赖

### macOS

```bash
brew install portaudio ffmpeg
brew install espeak-ng
```

### Ubuntu / Debian

```bash
sudo apt install portaudio19-dev ffmpeg
sudo apt install espeak-ng
```

各依赖的作用：
- `portaudio` → CLI 语音模式的麦克风输入与播放
- `ffmpeg` → TTS 音频转换
- `espeak-ng` → NeuTTS 的 phonemizer 后端

## 第四步：选择 STT 和 TTS 提供商

Hermes 同时支持本地和云端语音处理方案。

### 最简单 / 最低成本的方案

使用本地 STT 和免费的 Edge TTS：
- STT provider：`local`
- TTS provider：`edge`

这通常是最好的起点。

### 环境变量文件示例

添加到 `~/.hermes/.env`：

```bash
# 云端 STT 选项（本地无需密钥）
GROQ_API_KEY=***
VOICE_TOOLS_OPENAI_KEY=***

# 高级 TTS（可选）
ELEVENLABS_API_KEY=***
```

### Provider 推荐

#### 语音转文字（STT）

- `local` → 隐私保护和零成本使用的最佳默认选项
- `groq` → 极快的云端转录
- `openai` → 良好的付费备选

#### 文字转语音（TTS）

- `edge` → 免费，对大多数用户已足够
- `neutts` → 免费的本地/设备端 TTS
- `elevenlabs` → 最佳质量
- `openai` → 良好的中间选项
- `mistral` → 多语言，原生 Opus

### 如果使用 `hermes setup`

设置向导通过 PM 请求声明的 Python extras，不能绕过版本或平台限制：

声明的 `neutts` 依赖要求 Python 低于 3.14，而 Hermes 运行时要求 Python 3.14。
因此该 extra 不会在 Hermes 运行时安装 NeuTTS。请选择兼容的提供商。
独立的 NeuTTS 命令提供商需要自行管理兼容的 Python 环境。

如果依赖不支持当前平台，请选择其他提供商。

## 第五步：推荐配置

```yaml
voice:
  record_key: "ctrl+b"
  submit_mode: "direct"  # TUI：direct | draft
  max_recording_seconds: 120
  auto_tts: false
  beep_enabled: true
  silence_threshold: 200
  silence_duration: 3.0

stt:
  provider: "local"
  local:
    model: "base"

tts:
  provider: "edge"
  edge:
    voice: "en-US-AriaNeural"
```

这是适合大多数人的保守默认配置。

在 TUI 中，`voice.submit_mode` 控制转写完成后的行为：

- `direct`（默认）会立即提交转写文本。
- `draft` 会把转写文本放入输入框，供你编辑或取消，按 Enter 后才发送。

如需可编辑的语音草稿，请设置：

```yaml
voice:
  submit_mode: "draft"
```

如果想改用本地 TTS，将 `tts` 块替换为：

```yaml
tts:
  provider: "neutts"
  neutts:
    ref_audio: ''
    ref_text: ''
    model: neuphonic/neutts-air-q4-gguf
    device: cpu
```

## 使用场景一：CLI 语音模式

## 开启方式

启动 Hermes：

```bash
hermes
```

在 CLI 内执行：

```text
/voice on
```

### 录音流程

默认按键：
- `Ctrl+B`

工作流程：
1. 按下 `Ctrl+B`
2. 说话
3. 等待静音检测自动停止录音
4. Hermes 转录并回复
5. 如果开启了 TTS，它会朗读答案
6. 循环可自动重启以持续使用

### 常用命令

```text
/voice
/voice on
/voice off
/voice tts
/voice status
```

### 推荐的 CLI 工作流

#### 随走随调试

说：

```text
I keep getting a docker permission error. Help me debug it.
```

然后继续免手持操作：
- "再读一遍最后的错误"
- "用更简单的语言解释根本原因"
- "现在给我精确的修复方案"

#### 研究 / 头脑风暴

非常适合：
- 边走动边思考
- 口述半成形的想法
- 让 Hermes 实时整理你的思路

#### 无障碍 / 少打字场景

如果打字不方便，语音模式是保持完整 Hermes 工作流的最快方式之一。

## 调整 CLI 行为

### 静音阈值

如果 Hermes 开始/停止过于激进，调整：

```yaml
voice:
  silence_threshold: 250
```

阈值越高 = 灵敏度越低。

### 静音时长

如果你在句子之间经常停顿，增大该值：

```yaml
voice:
  silence_duration: 4.0
```

### 录音按键

如果 `Ctrl+B` 与你的终端或 tmux 习惯冲突：

```yaml
voice:
  record_key: "ctrl+space"
```


## 语音质量建议

### 最佳质量方案

- STT：本地 `large-v3` 或 Groq `whisper-large-v3`
- TTS：ElevenLabs

### 最佳速度 / 便利性方案

- STT：本地 `base` 或 Groq
- TTS：Edge

### 最佳零成本方案

- STT：本地
- TTS：Edge

## 常见故障模式

### "No audio device found"

安装 `portaudio`。


### "能转录但不说话"

检查：
- TTS provider 配置
- ElevenLabs 或 OpenAI 的 API 密钥 / 配额
- Edge 转换路径的 `ffmpeg` 安装情况

### "Whisper 输出乱码"

尝试：
- 更安静的环境
- 提高 `silence_threshold`
- 更换 STT provider/模型
- 更短、更清晰的表达


## 建议的第一周方案

如果你想走最短的成功路径：

1. 让文本 Hermes 正常工作
2. 运行 `hermes setup voice` 以启用语音支持
3. 使用本地 STT + Edge TTS 的 CLI 语音模式


这种递进方式可以将调试范围控制到最小。

## 下一步阅读

- [语音模式功能参考](../user-guide/features/voice-mode.md)
- [配置](../user-guide/configuration.md)