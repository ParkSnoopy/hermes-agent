---
sidebar_position: 10
title: "语音模式"
description: "与 Hermes Agent 进行实时语音对话 — CLI、Telegram、Discord（私信、文字频道和语音频道）"
---

# 语音模式

Hermes Agent 支持在 CLI 和消息平台上进行完整的语音交互。通过麦克风与 Agent 对话，听取语音回复，并在 Discord 语音频道中进行实时语音对话。

如需包含推荐配置和实际使用模式的实践指南，请参阅 [使用 Hermes 的语音模式](../../guides/use-voice-mode-with-hermes.md)。

## 前提条件

使用语音功能前，请确保已完成以下准备：

1. **已安装 Hermes Agent** — 通过安装脚本（参见 [安装](../../getting-started/installation.md)）
2. **已配置 LLM 提供商** — 运行 `hermes model` 或在 `~/.hermes/.env` 中设置首选提供商的凭据
3. **基础设置正常** — 运行 `hermes` 验证 Agent 能够响应文字消息，再启用语音功能

:::tip
`~/.hermes/` 目录和默认的 `config.yaml` 会在首次运行 `hermes` 时自动创建。只需手动创建 `~/.hermes/.env` 来存放 API 密钥。
:::

:::tip Nous Portal 同时覆盖两项
付费的 [Nous Portal](./tool-gateway.md) 订阅通过 Tool Gateway 同时提供 LLM（第 2 步）**和** OpenAI TTS — 无需单独的 OpenAI 密钥。全新安装时，`hermes setup --portal` 可一次性完成两项配置。
:::

## 概览

| 功能 | 平台 | 说明 |
|---------|----------|-------------|
| **交互式语音** | CLI | 按 Ctrl+B 开始录音，Agent 自动检测静音并回复 |
| **自动语音回复** | Telegram、Discord | Agent 在文字回复的同时发送语音音频 |
| **语音频道** | Discord | Bot 加入语音频道，监听用户发言并语音回复 |

## 环境要求

### Python 包

通过 `hermes tools` 配置语音提供商。缺失的内置功能依赖由 PM 按策略和目标平台支持准备。
如果选择的环境改变，请按提示重启 Hermes。
桌面包预装其支持的引擎；Docker 使用较小集合并关闭按需安装。
不要修改签名载荷或系统 Python。手动开发环境参见[开发配置](../../developer-guide/contributing.md)。

| Extra | 包 | 用途 |
|---|---|---|
| `voice` | `sounddevice`、`numpy`，以及支持平台上的 Faster-Whisper | CLI 音频和本地 STT |
| `audio-io` | `sounddevice`、`numpy` | 不包含本地 STT 的麦克风和播放支持 |
| `messaging` | `discord.py[voice]`、`python-telegram-bot`、`aiohttp` | Discord 和 Telegram |
| `tts-premium` | `elevenlabs` | ElevenLabs TTS |

原生 Windows ARM64 和 Intel macOS 不包含 Faster-Whisper，请使用云端或命令式 STT。
`all` 不代表所有语音或唤醒引擎。NeuTTS 是单独的可选运行时，首次使用会下载模型。

:::info
`discord.py[voice]` 会自动安装 **PyNaCl**（用于语音加密）和 **opus 绑定**。这是 Discord 语音频道支持的必要条件。
:::

### 系统依赖

```bash
# macOS
brew install portaudio ffmpeg opus
brew install espeak-ng   # for NeuTTS

# Ubuntu/Debian
sudo apt install portaudio19-dev ffmpeg libopus0
sudo apt install espeak-ng   # for NeuTTS
```

| 依赖项 | 用途 | 适用场景 |
|-----------|---------|-------------|
| **PortAudio** | 麦克风输入和音频播放 | CLI 语音模式 |
| **ffmpeg** | 音频格式转换（MP3 → Opus、PCM → WAV） | 所有平台 |
| **Opus** | Discord 语音编解码器 | Discord 语音频道 |
| **espeak-ng** | Phonemizer 后端 | 本地 NeuTTS 提供商 |

### API 密钥

添加到 `~/.hermes/.env`：

```bash
# 语音转文字（STT）— 本地提供商完全不需要密钥
# 本地 Faster-Whisper 由 PM 在支持的目标上准备，无需 STT API key。
GROQ_API_KEY=your-key                 # Groq Whisper — 速度快，有免费额度（云端）
VOICE_TOOLS_OPENAI_KEY=your-key       # OpenAI Whisper — 付费（云端）

# 文字转语音（TTS，可选 — Edge TTS 和 NeuTTS 无需任何密钥）
ELEVENLABS_API_KEY=***           # ElevenLabs — 高级音质
# 上方的 VOICE_TOOLS_OPENAI_KEY 同时启用 OpenAI TTS
```

:::tip
如果已安装 `faster-whisper`，语音模式的 STT 无需任何 API 密钥即可运行。模型（`base` 约 150 MB）会在首次使用时自动下载。
:::

---

## CLI 语音模式

语音模式在**经典 CLI**（`hermes chat`）和 **TUI**（`hermes --tui`）中均可使用。两者行为完全一致 — 相同的斜杠命令、相同的 VAD（语音活动检测）静音检测、相同的流式 TTS、相同的幻觉过滤器。TUI 额外将崩溃诊断日志转发至 `~/.hermes/logs/`，以便在异常音频后端出现按键录音失败时提供完整堆栈跟踪，而非静默消失。

### 快速开始

启动 CLI 并启用语音模式：

```bash
hermes                # 启动交互式 CLI
```

然后在 CLI 中使用以下命令：

```
/voice          切换语音模式开/关
/voice on       启用语音模式
/voice off      禁用语音模式
/voice tts      切换 TTS 输出
/voice status   显示当前状态
```

### 工作原理

1. 使用 `hermes` 启动 CLI，并通过 `/voice on` 启用语音模式
2. **按下 Ctrl+B** — 播放提示音（880Hz），开始录音
3. **开始说话** — 实时音频电平条显示输入状态：`● [▁▂▃▅▇▇▅▂] ❯`
4. **停止说话** — 静音 3 秒后自动停止录音
5. **两声提示音**（660Hz）确认录音结束
6. 音频通过 Whisper 转录后发送给 Agent
7. 如果启用了 TTS，Agent 的回复将以语音朗读
8. 录音**自动重新开始** — 无需按任何键即可继续说话

此循环持续进行，直到在录音过程中按下 **Ctrl+B**（退出连续模式），或连续 3 次录音均未检测到语音为止。

:::tip
录音键可通过 `~/.hermes/config.yaml` 中的 `voice.record_key` 配置（默认：`ctrl+b`）。
:::

### 静音检测

两阶段算法检测您是否已停止说话：

1. **语音确认** — 等待音频 RMS 值超过阈值（200）至少 0.3 秒，允许音节间的短暂停顿
2. **结束检测** — 语音确认后，持续静音 3.0 秒即触发停止

如果 15 秒内完全未检测到语音，录音自动停止。

`silence_threshold` 和 `silence_duration` 均可在 `config.yaml` 中配置。也可通过 `voice.beep_enabled: false` 禁用录音开始/结束提示音。

### 流式 TTS

启用 TTS 后，Agent 在生成文字的同时**逐句**朗读回复 — 无需等待完整响应：

1. 将文字增量缓冲为完整句子（最少 20 个字符）
2. 去除 Markdown 格式和 `<think>` 块
3. 实时逐句生成并播放音频

### 幻觉过滤器

Whisper 有时会从静音或背景噪音中生成幻觉文字（如"Thank you for watching"、"Subscribe"等）。Agent 使用包含 26 个已知幻觉短语（覆盖多种语言）的列表以及能捕获重复变体的正则表达式模式对其进行过滤。

---

## 配置参考

### config.yaml

```yaml
# 语音录制（CLI）
voice:
  record_key: "ctrl+b"            # 开始/停止录音的按键
  max_recording_seconds: 120       # 最大录音时长
  auto_tts: false                  # 启用语音模式时自动开启 TTS
  beep_enabled: true               # 播放录音开始/结束提示音
  silence_threshold: 200           # 静音判定的 RMS 电平（0-32767）
  silence_duration: 3.0            # 自动停止前的静音秒数

# 语音转文字（STT）
stt:
  enabled: true                     # 设为 false 可跳过自动转录 —
                                    # gateway 仍会缓存音频文件并将其路径
                                    # 作为入站消息的一部分传递给 Agent，
                                    # 适用于自定义流水线
                                    # （说话人分离、对齐、归档等）
  provider: "local"                  # "local"（免费）| "groq" | "openai" | "mistral" | "xai"
  local:
    model: "base"                    # tiny, base, small, medium, large-v3, turbo
  # model: "whisper-1"              # 旧版：在未设置 provider 时使用

# 文字转语音（TTS）
tts:
  provider: "edge"                 # "edge"（免费）| "elevenlabs" | "openai" | "neutts" | "minimax" | "mistral" | "gemini" | "xai" | "kittentts" | "piper"
  edge:
    voice: "en-US-AriaNeural"      # 322 种声音，74 种语言
  elevenlabs:
    voice_id: "pNInz6obpgDQGcFmaJgB"    # Adam
    model_id: "eleven_multilingual_v2"
  openai:
    model: "gpt-4o-mini-tts"
    voice: "alloy"                 # alloy, echo, fable, onyx, nova, shimmer
    base_url: "https://api.openai.com/v1"  # 可选：覆盖为自托管或兼容 OpenAI 的端点
  neutts:
    ref_audio: ''
    ref_text: ''
    model: neuphonic/neutts-air-q4-gguf
    device: cpu
```

### 环境变量

```bash
# 语音转文字提供商（本地无需密钥）
# 本地 Faster-Whisper 由 PM 在支持的目标上准备，无需 STT API key。
GROQ_API_KEY=...                    # Groq Whisper（速度快，有免费额度）
VOICE_TOOLS_OPENAI_KEY=...         # OpenAI Whisper（付费）

# STT 高级覆盖（可选）
STT_GROQ_MODEL=whisper-large-v3-turbo    # 覆盖默认 Groq STT 模型
STT_OPENAI_MODEL=whisper-1               # 覆盖默认 OpenAI STT 模型
GROQ_BASE_URL=https://api.groq.com/openai/v1     # 自定义 Groq 端点
STT_OPENAI_BASE_URL=https://api.openai.com/v1    # 自定义 OpenAI STT 端点

# 文字转语音提供商（Edge TTS 和 NeuTTS 无需密钥）
ELEVENLABS_API_KEY=***             # ElevenLabs（高级音质）
# 上方的 VOICE_TOOLS_OPENAI_KEY 同时启用 OpenAI TTS

# Discord 语音频道
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_USERS=...
```

### STT 提供商对比

| 提供商 | 模型 | 速度 | 质量 | 费用 | 需要 API 密钥 |
|----------|-------|-------|---------|------|---------|
| **本地** | `base` | 快（取决于 CPU/GPU） | 良好 | 免费 | 否 |
| **本地** | `small` | 中等 | 较好 | 免费 | 否 |
| **本地** | `large-v3` | 慢 | 最佳 | 免费 | 否 |
| **Groq** | `whisper-large-v3-turbo` | 非常快（约 0.5 秒） | 良好 | 免费额度 | 是 |
| **Groq** | `whisper-large-v3` | 快（约 1 秒） | 较好 | 免费额度 | 是 |
| **OpenAI** | `whisper-1` | 快（约 1 秒） | 良好 | 付费 | 是 |
| **OpenAI** | `gpt-4o-transcribe` | 中等（约 2 秒） | 最佳 | 付费 | 是 |
| **OpenAI** | `gpt-transcribe` | 快 | 最佳 | 付费（$0.0045/分钟） | 是 |

提供商优先级（自动回退）：**本地** > **groq** > **openai**

### TTS 提供商对比

| 提供商 | 质量 | 费用 | 延迟 | 需要密钥 |
|----------|---------|------|---------|-------------|
| **Edge TTS** | 良好 | 免费 | 约 1 秒 | 否 |
| **ElevenLabs** | 优秀 | 付费 | 约 2 秒 | 是 |
| **OpenAI TTS** | 良好 | 付费 | 约 1.5 秒 | 是 |
| **NeuTTS** | 良好 | 免费 | 取决于 CPU/GPU | 否 |

NeuTTS 使用上方的 `tts.neutts` 配置块。

---

## 故障排查

### "No audio device found"（CLI）

PortAudio 未安装：

```bash
brew install portaudio    # macOS
sudo apt install portaudio19-dev  # Ubuntu
```

### Whisper 返回乱码文字

幻觉过滤器会自动处理大多数情况。如果仍然出现幻觉转录：

- 在更安静的环境中使用
- 在配置中调高 `silence_threshold`（值越高，灵敏度越低）
- 尝试不同的 STT 模型
