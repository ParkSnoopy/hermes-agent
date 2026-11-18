---
sidebar_position: 2
title: "Environment Variables"
description: "Complete reference of all environment variables used by Hermes Agent"
---

# Environment Variables Reference

Hermes reads environment variables from the process environment and, for user-managed secrets, from `~/.hermes/.env`. Keep API keys, bot tokens, OAuth secrets, and other credentials in `.env`; prefer `config.yaml` for non-secret behaviour settings when a config key exists. Some variables below are process-only overrides or internal bridge variables and should not be committed to `.env` just because they are documented here.

## LLM Providers

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (recommended for flexibility) |
| `OPENROUTER_BASE_URL` | Override the OpenRouter-compatible base URL |
| `FIREWORKS_API_KEY` | Fireworks AI API key ([app.fireworks.ai](https://app.fireworks.ai/settings/users/api-keys)). Configure endpoint overrides with `model.base_url` in `config.yaml`. |
| `HERMES_OPENROUTER_CACHE` | Enable OpenRouter response caching (`1`/`true`/`yes`/`on`). Overrides `openrouter.response_cache` in config.yaml. See [Response Caching](https://openrouter.ai/docs/guides/features/response-caching). |
| `HERMES_OPENROUTER_CACHE_TTL` | Cache TTL in seconds (1-86400). Overrides `openrouter.response_cache_ttl` in config.yaml. |
| `NOUS_BASE_URL` | Override Nous Portal base URL (rarely needed; development/testing only) |
| `NOUS_INFERENCE_BASE_URL` | Override Nous inference endpoint directly |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway API key ([ai-gateway.vercel.sh](https://ai-gateway.vercel.sh)) |
| `AI_GATEWAY_BASE_URL` | Override AI Gateway base URL (default: `https://ai-gateway.vercel.sh/v1`) |
| `OPENAI_API_KEY` | OpenAI API key (`openai-api` provider), or the key for a custom OpenAI-compatible endpoint when `OPENAI_BASE_URL` is set; bound that way, it is sent only to that URL's exact origin (scheme, host and port), never to another port or to `http://` on the same host. Counts as an OpenRouter key only when it starts with `sk-or-`; put OpenRouter keys in `OPENROUTER_API_KEY` |
| `OPENAI_BASE_URL` | Base URL for custom endpoint (VLLM, SGLang, etc.) |
| `HERMES_CODEX_BASE_URL` | Route the `openai-codex` (ChatGPT subscription) provider through a proxy instead of the default Codex backend. Applies everywhere the credential is used: pool resolution, auxiliary/raw clients, and 401/429 credential rotation. `model.base_url` under `model.provider: openai-codex` is the secondary override when this is unset. |
| `LM_API_KEY` | API key for LM Studio (`lmstudio` provider). Often a placeholder for local servers |
| `LM_BASE_URL` | LM Studio base URL (default: `http://localhost:1234/v1`) |
| `GH_TOKEN` | GitHub token (also used by the `gh` CLI); honoured wherever Hermes reads `GITHUB_TOKEN` |
| `GITHUB_TOKEN` | GitHub token for GitHub API access (Skills Hub, git credentials, update checks; see the Skills Hub row below) |
| `GLM_API_KEY` | z.ai / ZhipuAI GLM API key ([z.ai](https://z.ai)) |
| `ZAI_API_KEY` | Alias for `GLM_API_KEY` |
| `Z_AI_API_KEY` | Alias for `GLM_API_KEY` |
| `GLM_BASE_URL` | Override z.ai base URL (default: `https://api.z.ai/api/paas/v4`) |
| `KIMI_API_KEY` | Kimi / Moonshot AI API key ([moonshot.ai](https://platform.moonshot.ai)) |
| `KIMI_CODING_API_KEY` | Alias key for the `kimi-coding` provider (accepted alongside `KIMI_API_KEY`) |
| `KIMI_BASE_URL` | Override Kimi base URL (default: `https://api.moonshot.ai/v1`) |
| `KIMI_CN_API_KEY` | Kimi / Moonshot China API key ([moonshot.cn](https://platform.moonshot.cn)) |
| `ARCEEAI_API_KEY` | Arcee AI API key ([chat.arcee.ai](https://chat.arcee.ai/)) |
| `ARCEE_BASE_URL` | Override Arcee base URL (default: `https://api.arcee.ai/api/v1`) |
| `GMI_API_KEY` | GMI Cloud API key ([gmicloud.ai](https://www.gmicloud.ai/)) |
| `GMI_BASE_URL` | Override GMI Cloud base URL (default: `https://api.gmi-serving.com/v1`) |
| `ACTUAL_API_KEY` | Actual Computer inference key (`ac_...`, [actual.inc/user/keys](https://actual.inc/user/keys)). Not needed for the local daemon. |
| `ACTUAL_BASE_URL` | Legacy fallback for the Actual base URL. Configure `model.provider: actual` and `model.base_url` in `config.yaml` instead; the YAML URL takes precedence. Defaults to `https://api.actual.inc/v1`. |
| `MINIMAX_API_KEY` | MiniMax API key — global endpoint ([minimax.io](https://www.minimax.io)). **Not used by `minimax-oauth`** (OAuth path uses browser login instead). |
| `MINIMAX_BASE_URL` | Override MiniMax base URL (default: `https://api.minimax.io/anthropic` — Hermes uses MiniMax's Anthropic Messages-compatible endpoint). **Not used by `minimax-oauth`**. |
| `MINIMAX_CN_API_KEY` | MiniMax API key — China endpoint ([minimaxi.com](https://www.minimaxi.com)). **Not used by `minimax-oauth`** (OAuth path uses browser login instead). |
| `MINIMAX_CN_BASE_URL` | Override MiniMax China base URL (default: `https://api.minimaxi.com/anthropic`). **Not used by `minimax-oauth`**. |
| `KILOCODE_API_KEY` | Kilo Code API key ([kilo.ai](https://kilo.ai)) |
| `KILOCODE_BASE_URL` | Override Kilo Code base URL (default: `https://api.kilo.ai/api/gateway`) |
| `XIAOMI_API_KEY` | Xiaomi MiMo API key ([platform.xiaomimimo.com](https://platform.xiaomimimo.com)) |
| `XIAOMI_BASE_URL` | Override Xiaomi MiMo base URL (default: `https://api.xiaomimimo.com/v1`) |
| `UPSTAGE_API_KEY` | Upstage API key for Solar models ([console.upstage.ai](https://console.upstage.ai/api-keys)) |
| `UPSTAGE_BASE_URL` | Override Upstage base URL (default: `https://api.upstage.ai/v1`) |
| `TOKENHUB_API_KEY` | Tencent TokenHub API key ([tokenhub.tencentmaas.com](https://tokenhub.tencentmaas.com)) |
| `TOKENHUB_BASE_URL` | Override Tencent TokenHub base URL (default: `https://tokenhub.tencentmaas.com/v1`) |
| `TOKENPLAN_API_KEY` | Tencent TokenPlan API key (LKEAP; Anthropic Messages endpoint) |
| `TOKENPLAN_BASE_URL` | Override Tencent TokenPlan base URL (default: `https://api.lkeap.cloud.tencent.com/plan/anthropic`) |
| `AZURE_FOUNDRY_API_KEY` | Microsoft Foundry / Azure OpenAI API key ([ai.azure.com](https://ai.azure.com/)). Not needed when `model.auth_mode: entra_id` |
| `AZURE_FOUNDRY_BASE_URL` | Microsoft Foundry endpoint URL (e.g. `https://<resource>.openai.azure.com/openai/v1` for OpenAI-style, or `https://<resource>.services.ai.azure.com/anthropic` for Anthropic-style) |
| `AZURE_ANTHROPIC_KEY` | Azure Anthropic API key for `provider: anthropic` + `base_url` pointing at a Microsoft Foundry Claude deployment (alternative to `ANTHROPIC_API_KEY` when both Anthropic and Azure Anthropic are configured) |
| `AZURE_TENANT_ID` | Entra ID tenant ID (service-principal flows; honored by `azure-identity` when `model.auth_mode: entra_id`) |
| `AZURE_CLIENT_ID` | Entra ID client ID (service principal, workload identity, or user-assigned managed identity) |
| `AZURE_CLIENT_SECRET` | Service principal secret used by `EnvironmentCredential` |
| `AZURE_CLIENT_CERTIFICATE_PATH` | Service principal certificate (alternative to `AZURE_CLIENT_SECRET`) |
| `AZURE_FEDERATED_TOKEN_FILE` | Federated token file path for AKS Workload Identity / OIDC flows |
| `AZURE_AUTHORITY_HOST` | Sovereign-cloud authority override (e.g. `https://login.microsoftonline.us` for Azure Government). See [Azure Foundry guide](../guides/azure-foundry.md#sovereign-clouds-government-china) |
| `IDENTITY_ENDPOINT` / `MSI_ENDPOINT` | Managed Identity endpoint for App Service, Functions, and Container Apps; VMs usually use IMDS instead and do not set these |
| `HF_TOKEN` | Hugging Face token for Inference Providers ([huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)) |
| `HF_BASE_URL` | Override Hugging Face base URL (default: `https://router.huggingface.co/v1`) |
| `GOOGLE_API_KEY` | Google AI Studio API key ([aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)) |
| `GEMINI_API_KEY` | Alias for `GOOGLE_API_KEY` |
| `GEMINI_BASE_URL` | Override Google AI Studio base URL |
| `VERTEX_CREDENTIALS_PATH` | Path to a Google Cloud service account JSON for Vertex AI (Gemini). Vertex uses OAuth2, not a static API key. Falls back to `GOOGLE_APPLICATION_CREDENTIALS`, then to ADC (`gcloud auth application-default login`). Set project/region under `vertex:` in `config.yaml` |
| `ANTHROPIC_API_KEY` | Anthropic Console API key ([console.anthropic.com](https://console.anthropic.com/)) |
| `ANTHROPIC_BASE_URL` | Override the Anthropic API base URL |
| `ANTHROPIC_TOKEN` | Manual or legacy Anthropic OAuth/setup-token override |
| `DASHSCOPE_API_KEY` | Qwen Cloud (Alibaba DashScope) API key for Qwen models ([modelstudio.console.alibabacloud.com](https://modelstudio.console.alibabacloud.com/)) |
| `DASHSCOPE_BASE_URL` | Custom DashScope base URL (default: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`; use `https://dashscope.aliyuncs.com/compatible-mode/v1` for mainland-China region) |
| `DASHSCOPE_CN_BASE_URL` | Override the `alibaba-cn` mainland-China DashScope base URL |
| `ALIBABA_CODING_PLAN_API_KEY` | Qwen Coding Plan API key (`alibaba-coding-plan`; also a fallback for `alibaba-coding-plan-cn`) |
| `ALIBABA_CODING_PLAN_CN_API_KEY` | Qwen Coding Plan API key for the mainland-China `alibaba-coding-plan-cn` provider (checked before the shared key, so only the CN row lights up) |
| `ALIBABA_CODING_PLAN_BASE_URL` | Override the Qwen Coding Plan base URL (international) |
| `ALIBABA_CODING_PLAN_CN_BASE_URL` | Override the Qwen Coding Plan base URL (mainland China) |
| `ALIBABA_TOKEN_PLAN_API_KEY` | Alibaba Model Studio Token Plan API key (`alibaba-token-plan`; also a fallback for `alibaba-token-plan-cn`) |
| `ALIBABA_TOKEN_PLAN_CN_API_KEY` | Token Plan API key for the mainland-China `alibaba-token-plan-cn` provider (checked before the shared key) |
| `ALIBABA_TOKEN_PLAN_BASE_URL` | Override the Token Plan base URL (international) |
| `ALIBABA_TOKEN_PLAN_CN_BASE_URL` | Override the Token Plan base URL (mainland China) |
| `DEEPSEEK_API_KEY` | DeepSeek API key for direct DeepSeek access ([platform.deepseek.com](https://platform.deepseek.com/api_keys)) |
| `DEEPSEEK_BASE_URL` | Custom DeepSeek API base URL |
| `DEEPINFRA_API_KEY` | DeepInfra API key ([deepinfra.com](https://deepinfra.com/dash/api_keys)) |
| `DEEPINFRA_BASE_URL` | DeepInfra base URL override |
| `NOVITA_API_KEY` | NovitaAI API key — AI-native cloud for Model API, Agent Sandbox, and GPU Cloud ([novita.ai/settings/key-management](https://novita.ai/settings/key-management)) |
| `NOVITA_BASE_URL` | Override NovitaAI base URL (default: `https://api.novita.ai/openai/v1`) |
| `RAMP_ROUTER_API_KEY` | Ramp Router API key ([app.router.com/keys](https://app.router.com/keys)); alias `ROUTER_API_KEY` also accepted |
| `RAMP_ROUTER_BASE_URL` | Override Ramp Router base URL (default: `https://api.router.com/v1`) |
| `NEBIUS_API_KEY` | Nebius Token Factory API key ([tokenfactory.nebius.com](https://tokenfactory.nebius.com/)); `NEBIUS_TOKEN_FACTORY_API_KEY` also accepted |
| `NEBIUS_BASE_URL` | Override Nebius Token Factory base URL (default: `https://api.tokenfactory.nebius.com/v1`) |
| `NVIDIA_API_KEY` | NVIDIA NIM API key — Nemotron and open models ([build.nvidia.com](https://build.nvidia.com)) |
| `NVIDIA_BASE_URL` | Override NVIDIA base URL (default: `https://integrate.api.nvidia.com/v1`; set to `http://localhost:8000/v1` for a local NIM endpoint) |
| `STEPFUN_API_KEY` | StepFun API key — Step-series models ([platform.stepfun.com](https://platform.stepfun.com)) |
| `STEPFUN_BASE_URL` | Override StepFun base URL (default: `https://api.stepfun.com/v1`) |
| `OLLAMA_API_KEY` | Ollama Cloud API key — managed Ollama catalog without local GPU ([ollama.com/settings/keys](https://ollama.com/settings/keys)) |
| `OLLAMA_BASE_URL` | Override Ollama Cloud base URL (default: `https://ollama.com/v1`) |
| `XAI_API_KEY` | xAI (Grok) API key for chat + web search ([console.x.ai](https://console.x.ai/)) |
| `XAI_BASE_URL` | Override xAI base URL (default: `https://api.x.ai/v1`) |
| `MISTRAL_API_KEY` | Mistral API key for model access ([console.mistral.ai](https://console.mistral.ai)) |
| `AWS_REGION` | AWS region for Bedrock inference (e.g. `us-east-1`, `eu-central-1`). Read by boto3. |
| `AWS_PROFILE` | AWS named profile for Bedrock authentication (reads `~/.aws/credentials`). Leave unset to use default boto3 credential chain. |
| `BEDROCK_BASE_URL` | Override Bedrock runtime base URL (default: `https://bedrock-runtime.us-east-1.amazonaws.com`; usually leave unset and use `AWS_REGION` instead) |
| `HERMES_QWEN_BASE_URL` | Qwen Portal base URL override (default: `https://portal.qwen.ai/v1`) |
| `OPENCODE_ZEN_API_KEY` | OpenCode Zen API key — pay-as-you-go access to curated models ([opencode.ai](https://opencode.ai/auth)) |
| `OPENCODE_ZEN_BASE_URL` | Override OpenCode Zen base URL |
| `OPENCODE_GO_API_KEY` | OpenCode Go API key — $10/month subscription for open models ([opencode.ai](https://opencode.ai/auth)) |
| `OPENCODE_GO_BASE_URL` | Override OpenCode Go base URL |
| `CLAUDE_CODE_OAUTH_TOKEN` | Explicit Claude Code token override if you export one manually |
| `HERMES_MODEL` | Override model name at process level (prefer `config.yaml` for normal use) |
| `HERMES_HOME` | Select the configuration and user-data home. A literal `~` or `$VAR` in the value is expanded (fish does not expand `~` inside `VAR=~/…`), so it never resolves relative to the current directory. Defaults to `~/.hermes` on POSIX and `%LOCALAPPDATA%\hermes` on Windows. Profile/runtime context can select a more specific home. |
| `HERMES_CONFIG_BACKEND` | Select where the profile's `config.yaml` user layer is read and written. `file` (the default) is the local `config.yaml`; `remote` is [Remote Config](../user-guide/remote-config.md). Any other value, or a remote backend that cannot serve config, stops Hermes at startup with an error instead of falling back to defaults. An explicitly empty value in a `.env` resets a stale shell value to `file`. Read from the launch environment, the launch home's `.env` and the managed `.env`, never from `config.yaml`. See [Config backend](../developer-guide/config-backend.md). |
| `HERMES_CONFIG_REMOTE_URL` | Base URL of the [Remote Config](../user-guide/remote-config.md) plane (default `https://config-config.nousresearch.com`; `https://` only, plain `http://` only to loopback, no user info). An env var because it is needed before any config can be read: it says where config comes from. |
| `HERMES_CONFIG_INSTANCE_ID` | This agent's id in the [Remote Config](../user-guide/remote-config.md) plane; required with `HERMES_CONFIG_BACKEND=remote`. An env var because the plane needs it to serve the first config read. |


## Provider Auth (OAuth)

For native Anthropic auth, Hermes prefers Claude Code's own credential files when they exist because those credentials can refresh automatically. **OAuth against Anthropic requires a Claude Max plan with purchased extra usage credits** — Hermes routes as Claude Code, which only draws from the Max plan's extra/overage credits, not the base Max allowance, and does not work on Claude Pro. Without Max + extra credits, use an API key instead. Environment variables such as `ANTHROPIC_TOKEN` remain useful as manual overrides, but they are no longer the preferred path for Claude Max login.

| Variable | Description |
|----------|-------------|
| `HERMES_PORTAL_BASE_URL` | Override Nous Portal URL (for development/testing). Per-profile under multiplexing: set it in the served profile's `.env`. |
| `NOUS_INFERENCE_BASE_URL` | Override Nous inference API URL. Also the only non-production host a Portal response may name: when the Portal's returned inference URL matches this override it is accepted and persisted instead of being healed to production. Per-profile under multiplexing. |
| `HERMES_NOUS_MIN_KEY_TTL_SECONDS` | Min agent key TTL before re-mint (default: 1800 = 30min) |
| `HERMES_NOUS_TIMEOUT_SECONDS` | HTTP timeout for Nous credential / token flows |
| `HERMES_DUMP_REQUESTS` | Dump API request payloads to log files (`true`/`false`) |
| `HERMES_PREFILL_MESSAGES_FILE` | Path to a JSON file of ephemeral prefill messages injected at API-call time |
| `HERMES_TIMEZONE` | IANA timezone override (for example `America/New_York`). On Linux/macOS it is also exported as `TZ` to `execute_code` children; on Windows those children keep the OS zone instead, because the Windows C runtime only understands POSIX-form `TZ` strings and mis-parses an IANA name into a wrong offset |

## Tool APIs

| Variable | Description |
|----------|-------------|
| `PARALLEL_API_KEY` | AI-native web search ([parallel.ai](https://parallel.ai/)) |
| `FIRECRAWL_API_KEY` | Web scraping and cloud browser ([firecrawl.dev](https://firecrawl.dev/)) |
| `FIRECRAWL_API_URL` | Custom Firecrawl API endpoint for self-hosted instances (optional) |
| `TAVILY_API_KEY` | Optional Tavily API key for higher search/extract limits. After selecting Tavily as the web backend, keyless access works without it ([app.tavily.com](https://app.tavily.com/home), [keyless docs](https://docs.tavily.com/documentation/keyless)) |
| `TAVILY_BASE_URL` | Override the Tavily API endpoint. Useful for corporate proxies and self-hosted Tavily-compatible search backends. Same pattern as `GROQ_BASE_URL`. |
| `PERPLEXITY_API_KEY` | Perplexity Search API key for the `perplexity` web backend — ranked search results plus query-relevant page snippets for extract ([perplexity.ai/account/api](https://www.perplexity.ai/account/api)) |
| `PERPLEXITY_BASE_URL` | Override the Perplexity API endpoint (default `https://api.perplexity.ai`) for proxies (optional) |
| `SEARXNG_URL` | SearXNG instance URL for free self-hosted web search — no API key required ([searxng.github.io](https://searxng.github.io/searxng/)) |
| `EXA_API_KEY` | Exa API key for AI-native web search and contents ([exa.ai](https://exa.ai/)) |
| `BRAVE_SEARCH_API_KEY` | Brave Search API subscription token for web search (free tier available) ([brave.com/search/api](https://brave.com/search/api/)) |
| `BROWSERBASE_API_KEY` | Browser automation ([browserbase.com](https://browserbase.com/)) |
| `BROWSERBASE_PROJECT_ID` | Browserbase project ID |
| `BROWSER_USE_API_KEY` | Browser Use cloud browser API key ([browser-use.com](https://browser-use.com/)) |
| `FIRECRAWL_BROWSER_TTL` | Firecrawl browser session TTL in seconds (default: 300) |
| `BROWSER_CDP_URL` | Chrome DevTools Protocol endpoint the browser tools attach to instead of launching Chromium (set via `/browser connect`, e.g. `ws://localhost:9222`; same as `browser.cdp_url`) |
| `CAMOFOX_*` | Read only by the Camofox plugin (`hermes plugins install camofox`); core Hermes ignores them. |
| `BROWSER_INACTIVITY_TIMEOUT` | Browser session inactivity timeout in seconds |
| `AGENT_BROWSER_*` | No longer read. On update, values in the profile's `.env` are copied into config when the key is unset (`browser.engine: auto` and `browser.headed: false` count as unset; a relative `AGENT_BROWSER_PROFILE` becomes an absolute path): `AGENT_BROWSER_EXECUTABLE_PATH` → `browser.executable_path`, `AGENT_BROWSER_PROFILE` → `browser.user_data_dir`, `AGENT_BROWSER_ARGS` → `browser.chromium_args`, `AGENT_BROWSER_HEADED` → `browser.headed`, `AGENT_BROWSER_ENGINE` → `browser.engine`. |
| `FAL_KEY` | Image generation ([fal.ai](https://fal.ai/)) |
| `KREA_API_KEY` | Krea API key for Krea 3 and Krea 2 image generation ([krea.ai](https://krea.ai/)) |
| `GITHUB_TOKEN` | GitHub token for Skills Hub (higher API rate limits, skill publish) and the desktop app's update check (`GH_TOKEN` also honoured; without either, the desktop falls back to the `gh` CLI login, then anonymous) |
| `HONCHO_API_KEY` | Cross-session user modeling ([honcho.dev](https://honcho.dev/)) |
| `HONCHO_BASE_URL` | Base URL for self-hosted Honcho instances (default: Honcho cloud). No API key required for local instances |
| `HINDSIGHT_API_KEY` | Hindsight API key for graph-aware persistent memory ([hindsight.vectorize.io](https://hindsight.vectorize.io)) |
| `HINDSIGHT_API_URL` | Base URL for the Hindsight API (default: `https://api.hindsight.vectorize.io`) |
| `HINDSIGHT_TIMEOUT` | Timeout in seconds for Hindsight memory-provider API calls (default: `60`). Bump this if your Hindsight instance is slow to respond during `/sync` or `on_session_switch` and you're seeing timeouts in `errors.log`. |
| `MEM0_API_KEY` | Mem0 Platform API key for semantic persistent memory ([app.mem0.ai](https://app.mem0.ai)) |
| `MEM0_MODE` | Mem0 backend mode: `platform` (default) or `oss` — see [Memory Providers](../user-guide/features/memory-providers.md) |
| `MEM0_HOST` | Base URL of a self-hosted Mem0 server (switches the plugin off the Platform API) |
| `MEM0_USER_ID` | Override the user id Mem0 memories are stored under |
| `MEM0_AGENT_ID` | Override the agent id Mem0 memories are tagged with |
| `RETAINDB_API_KEY` | RetainDB API key for persistent memory ([retaindb.com](https://retaindb.com)) |
| `RETAINDB_BASE_URL` | Base URL for self-hosted RetainDB instances (default: `https://api.retaindb.com`) |
| `OPENVIKING_API_KEY` | OpenViking API key (leave blank for local dev mode) |
| `OPENVIKING_ENDPOINT` | OpenViking server URL (default: `http://127.0.0.1:1933`) |
| `BRV_API_KEY` | ByteRover API key (optional, for cloud sync — local-first by default) ([app.byterover.dev](https://app.byterover.dev)) |
| `SUPERMEMORY_API_KEY` | Semantic long-term memory with profile recall and session ingest ([supermemory.ai](https://supermemory.ai)) |
| `SPRITES_TOKEN` | Sprites cloud sandboxes, read by the [`sprites` plugin](https://github.com/NousResearch/hermes-plugin-sprites) ([fly.io/sprites](https://fly.io/sprites)) |
| `VERCEL_TOKEN` | Vercel Sandbox access token ([vercel.com](https://vercel.com/)) |
| `VERCEL_PROJECT_ID` | Vercel project ID (required with `VERCEL_TOKEN`) |
| `VERCEL_TEAM_ID` | Vercel team ID (required with `VERCEL_TOKEN`) |
| `VERCEL_OIDC_TOKEN` | Vercel short-lived OIDC token (development-only alternative) |

### Skill API Keys

The optional telephony skill uses `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER`.

Secrets consumed by specific bundled / optional skills. Each is only needed if you use the corresponding skill.

| Variable | Used by skill | Description |
|----------|---------------|-------------|
| `NOTION_API_KEY` | Teams meeting pipeline | Notion integration token for the Notion sink. |
| `LINEAR_API_KEY` | `linear` | Linear personal API key. |
| `TENOR_API_KEY` | `gif-search` | Tenor API key for GIF search. |


### Nous Tool Gateway

These variables configure the [Tool Gateway](../user-guide/features/tool-gateway.md) for paid Nous subscribers or self-hosted gateway deployments. Most users don't need to set these — the gateway is configured automatically via `hermes model` or `hermes tools`.

| Variable | Description |
|----------|-------------|
| `TOOL_GATEWAY_DOMAIN` | Base domain for Tool Gateway routing (default: `nousresearch.com`) |
| `TOOL_GATEWAY_SCHEME` | HTTP or HTTPS scheme for gateway URLs (default: `https`) |
| `TOOL_GATEWAY_USER_TOKEN` | Auth token for the Tool Gateway (normally auto-populated from Nous auth) |
| `FIRECRAWL_GATEWAY_URL` | Override URL for the Firecrawl gateway endpoint specifically |

## Terminal Backend

Terminal commands run locally as the Hermes process user, with that user’s filesystem permissions. They are not isolated by Hermes. Explicit nonlocal terminal selectors are refused; remove or correct old configuration before running. Running Hermes itself on a remote server or in a container is a separate deployment choice.

| Variable | Description |
|----------|-------------|
| `TERMINAL_ENV` | Only `local` is supported; nonlocal values are refused. |
| `TERMINAL_TIMEOUT` | Command timeout in seconds |
| `TERMINAL_LIFETIME_SECONDS` | Idle session cleanup time in seconds |
| `TERMINAL_CWD` | Deprecated gateway override; prefer `terminal.cwd`. |
| `SUDO_PASSWORD` | Enable sudo without interactive prompt; stored in plaintext. |

## Persistent Shell

Exported env vars carry from one command to the next while on; the working directory always carries. See [Persistent Shell](../user-guide/configuration.md#persistent-shell).

| Variable | Description |
|----------|-------------|
| `TERMINAL_LOCAL_PERSISTENT` | Local backend: carry exported env vars between commands (default: `true`) |

## API and webhooks

| Variable | Description |
|----------|-------------|
| `WEBHOOK_ENABLED` | Enable the webhook platform adapter (`true`/`false`) |
| `WEBHOOK_PORT` | HTTP server port for receiving webhooks (default: `8644`) |
| `WEBHOOK_SECRET` | Global HMAC secret for webhook signature validation (used as fallback when routes don't specify their own) |
| `API_SERVER_ENABLED` | Enable the OpenAI-compatible API server (`true`/`false`). Runs alongside other platforms. |
| `API_SERVER_KEY` | Bearer token for API server authentication. Required whenever the API server is enabled. |
| `API_SERVER_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API server directly (for example `http://localhost:3000,http://127.0.0.1:3000`). Default: disabled. |
| `API_SERVER_PORT` | Port for the API server (default: `8642`) |
| `API_SERVER_HOST` | Host/bind address for the API server (default: `127.0.0.1`). `API_SERVER_KEY` is still required on loopback; use a narrow `API_SERVER_CORS_ORIGINS` allowlist for browser access. |
| `API_SERVER_MODEL_NAME` | Model name advertised on `/v1/models`. Defaults to the profile name (or `hermes-agent` for the default profile). Useful for multi-user setups where frontends like Open WebUI need distinct model names per connection. |

### Microsoft Graph (Teams Meetings)

App-only credentials for the Microsoft Graph REST client used by the upcoming Teams meeting summary pipeline. See [Register a Microsoft Graph application](../guides/microsoft-graph-app-registration.md) for the Azure portal walkthrough and the exact API permissions required.

| Variable | Description |
|----------|-------------|
| `MSGRAPH_TENANT_ID` | Azure AD tenant ID (directory GUID) for the Graph app registration. |
| `MSGRAPH_CLIENT_ID` | Application (client) ID of the Azure app registration. |
| `MSGRAPH_CLIENT_SECRET` | Client secret value for the app registration. Store in `~/.hermes/.env` with `chmod 600`; rotate periodically via the Azure portal. |
| `MSGRAPH_SCOPE` | OAuth2 scope for the client-credentials token request (default: `https://graph.microsoft.com/.default`). |
| `MSGRAPH_AUTHORITY_URL` | Microsoft identity platform authority (default: `https://login.microsoftonline.com`). Override only for national/sovereign clouds (e.g. `https://login.microsoftonline.us` for GCC High). |

### Microsoft Graph Webhook Listener

Inbound change-notification listener for Graph events (Teams meetings, calendar, chat, etc.).

| Variable | Description |
|----------|-------------|
| `MSGRAPH_WEBHOOK_ENABLED` | Enable the `msgraph_webhook` gateway platform (`true`/`1`/`yes`). |
| `MSGRAPH_WEBHOOK_PORT` | Port the listener binds to (default: `8646`). |
| `MSGRAPH_WEBHOOK_CLIENT_STATE` | Shared secret Graph echoes in every notification; compared with `hmac.compare_digest`. Generate with `openssl rand -hex 32`. |
| `MSGRAPH_WEBHOOK_ACCEPTED_RESOURCES` | Comma-separated allowlist of Graph resource paths/patterns (e.g. `communications/onlineMeetings,chats/*/messages`). Trailing `*` is prefix-matching. Empty = accept all. |
| `MSGRAPH_WEBHOOK_ALLOWED_SOURCE_CIDRS` | Comma-separated CIDR ranges allowed to POST to the listener (e.g. `52.96.0.0/14,52.104.0.0/14`). Empty = allow all (default). Restrict to Microsoft Graph's published egress ranges in production. |

### Teams Meeting Summary Delivery

Only used when the `teams_pipeline` plugin is enabled.
Set `platforms.msgraph_webhook.extra.meeting_pipeline.teams_delivery.enabled: true` to enable delivery.
The `TEAMS_*` variables below are fallbacks; values configured in `platforms.msgraph_webhook.extra.meeting_pipeline.teams_delivery` take precedence.

| Variable | Description |
|----------|-------------|
| `TEAMS_DELIVERY_MODE` | `graph` or `incoming_webhook`. |
| `TEAMS_INCOMING_WEBHOOK_URL` | Teams-generated webhook URL; required when `TEAMS_DELIVERY_MODE=incoming_webhook`. |
| `TEAMS_GRAPH_ACCESS_TOKEN` | Pre-acquired delegated access token for Graph delivery. Rarely needed — the writer falls back to the `MSGRAPH_*` app credentials when unset. |
| `TEAMS_TEAM_ID` | Target Team ID for channel delivery (`graph` mode). |
| `TEAMS_CHANNEL_ID` | Target channel ID (paired with `TEAMS_TEAM_ID`). |
| `TEAMS_CHAT_ID` | Target 1:1 or group chat ID (alternative to team+channel for `graph` mode). |

## NeMo Relay

| Variable | Description |
|----------|-------------|
| `HERMES_NEMO_RELAY_PLUGINS_TOML` | Optional explicit path to a standard NeMo Relay `plugins.toml` loaded process-wide by Hermes core. When unset, Relay uses its normal user configuration followed by the higher-precedence machine-wide system configuration. When set, this file replaces the user configuration while the system configuration still applies above it. See the [Relay integration note](../user-guide/features/built-in-plugins.md#nemo-relay-native-integration-migration-note). |

## Agent Behavior

| Variable | Description |
|----------|-------------|
| `HERMES_MAX_ITERATIONS` | Max tool-calling iterations per conversation (default: 500) |
| `HERMES_INFERENCE_MODEL` | Override model name at process level (takes priority over `config.yaml` for the session). Also settable via `-m`/`--model` flag. |
| `HERMES_YOLO_MODE` | Set to `1` to bypass dangerous-command approval prompts. Equivalent to `--yolo`. |
| `HERMES_ACCEPT_HOOKS` | Auto-approve any unseen shell hooks declared in `config.yaml` without a TTY prompt. Equivalent to `--accept-hooks` or `hooks_auto_accept: true`. |
| `HERMES_IGNORE_USER_CONFIG` | Skip `~/.hermes/config.yaml` and use built-in defaults (credentials in `.env` still load). Equivalent to `--ignore-user-config`. |
| `HERMES_IGNORE_RULES` | Skip auto-injection of `AGENTS.md`, `SOUL.md`, `.cursorrules`, memory, and preloaded skills. Equivalent to `--ignore-rules`. |
| `HERMES_SAFE_MODE` | Legacy in-process troubleshooting switch: skips plugin discovery, MCP server loading, and shell-hook registration for that process. `hermes chat --safe-mode` no longer sets it — the gateway freezes the safe policy into the session and executes it in an isolated worker instead, so the running daemon's environment is never mutated. |
| `HERMES_TOOL_PROGRESS` | Unsupported since the config-v12 support floor — the variable is ignored. Use `display.tool_progress` in `config.yaml`. |
| `HERMES_TOOL_PROGRESS_MODE` | Deprecated compatibility variable for tool progress mode (still read by the gateway as a fallback). Prefer `display.tool_progress` in `config.yaml`. |
| `HERMES_HUMAN_DELAY_MODE` / `HERMES_HUMAN_DELAY_MIN_MS` / `HERMES_HUMAN_DELAY_MAX_MS` | No longer read. Response pacing is the `human_delay` section of each profile's `config.yaml` (`mode`, `min_ms`, `max_ms`), so multiplexed profiles keep independent pacing. |
| `HERMES_QUIET` | Suppress non-essential output (`true`/`false`) |
| `CODEX_HOME` | When [Codex app-server runtime](../user-guide/features/codex-app-server-runtime) is enabled, override the directory Codex CLI reads its config + auth from (default: `~/.codex`). Hermes' migration writes the managed block to `<CODEX_HOME>/config.toml`. |
| `HERMES_ACP_SKIP_CONFIGURED_MCP` | Obsolete. No longer read, and has no effect. `hermes acp` is a viewer of the gateway daemon and never starts `config.yaml` MCP servers itself. The gateway starts its profile's `mcp_servers` at boot (filtered for ACP by `platform_toolsets.acp`), and the editor's `session/new` `mcpServers` are attached by the gateway either way. See [ACP host integration](../user-guide/features/acp#host-integration). |
| `HERMES_API_TIMEOUT` | LLM API call timeout in seconds (default: `1800`) |
| `HERMES_API_CALL_STALE_TIMEOUT` | Non-streaming stale-call timeout in seconds (default: `90`). Auto-disabled for local providers when left unset, and may scale upward for very large contexts. Also configurable via `providers.<id>.stale_timeout_seconds` or `providers.<id>.models.<model>.stale_timeout_seconds` in `config.yaml`. |
| `HERMES_STREAM_READ_TIMEOUT` | Streaming socket read timeout in seconds (default: `120`). Auto-increased to `HERMES_API_TIMEOUT` for local providers. Increase if local LLMs time out during long code generation. |
| `HERMES_STREAM_STALE_TIMEOUT` | Stale stream detection timeout in seconds (default: `180`). Auto-disabled for local providers. Triggers connection kill if no chunks arrive within this window. |
| `HERMES_LOCAL_STREAM_STALE_TIMEOUT` | Stale stream ceiling for local providers (Ollama, oMLX, llama-cpp) in seconds (default: `900`). When the base stale timeout is at its default and a local endpoint is detected, this finite ceiling replaces the former infinite disable so a wedged local server eventually trips the detector instead of hanging forever. Also configurable via `agent.local_stream_stale_timeout` in `config.yaml`. |
| `HERMES_STREAM_RETRIES` | Number of mid-stream reconnect attempts on transient network errors (default: `3`). |
| `HERMES_STREAM_STALE_GIVEUP` | Cross-turn circuit breaker: after this many consecutive stale kills (streaming or non-streaming) with no completed response, abort each call immediately with an actionable error instead of re-waiting out the stale timeout (default: `5`, `0` disables). Resets on any completed response, `/model` switch, fallback activation, or turn-start primary restore. |
| `HERMES_AGENT_TIMEOUT` | Gateway inactivity timeout for a running agent in seconds (default: `1800`, 30 minutes). Resets on every tool call and streamed token. Set to `0` to disable. |
| `HERMES_GATEWAY_MAX_STARTS` | Respawn-storm circuit breaker: maximum gateway (re)starts allowed within the window before an exponential backoff is slept to break the storm (default: `5`, `0` disables). Also configurable via `gateway.respawn_storm.max_starts` in `config.yaml`. |
| `HERMES_GATEWAY_START_WINDOW_S` | Respawn-storm breaker window in seconds (default: `120`). Also configurable via `gateway.respawn_storm.window_seconds` in `config.yaml`. |
| `HERMES_STARTUP_WATCHDOG` | Startup-liveness watchdog for `hermes gateway run`: if the process does not reach a live event loop within the timeout, holds no progress lease and shows no CPU progress, it dumps every thread's stack to `logs/gateway-startup-watchdog.log` and exits with code `75` so the service supervisor (systemd, s6, Windows task) restarts it. Set to `0` to opt out. Env-only because `config.yaml` parsing is itself inside the watched window; `gateway.startup_watchdog: false` in `config.yaml` is bridged into this variable when unset. |
| `HERMES_STARTUP_WATCHDOG_TIMEOUT_S` | Startup watchdog timeout in seconds (default: `300`). Slow-but-alive phases (state.db schema migrations, repair, construction-time archive/prune/VACUUM) hold their own progress leases, so raise this only when a large install's startup is legitimately longer than five minutes *outside* those phases (many multiplexed profiles, thousands of skills on a slow disk). Bridged from `gateway.startup_watchdog_timeout_seconds` in `config.yaml` when unset. |
| `HERMES_AGENT_TIMEOUT_WARNING` | Gateway: send a warning message after this many seconds of inactivity (default: 75% of `HERMES_AGENT_TIMEOUT`). |
| `HERMES_AGENT_NOTIFY_INTERVAL` | Gateway: interval in seconds between progress notifications on long-running agent turns. |
| `HERMES_CHECKPOINT_TIMEOUT` | Timeout for filesystem checkpoint creation in seconds (default: `30`). |
| `HERMES_EXEC_ASK` | Enable execution approval prompts in gateway mode (`true`/`false`) |
| `HERMES_ENABLE_PROJECT_PLUGINS` | Enable auto-discovery of repo-local plugins from `./.hermes/plugins/` for both the agent loader and the dashboard web server. Accepts the standard truthy set: `1` / `true` / `yes` / `on` (case-insensitive). Everything else — including `0`, `false`, `no`, `off`, and the empty string — is treated as **disabled** (default). Note: as of GHSA-5qr3-c538-wm9j (#29156) the dashboard web server refuses to auto-import a project plugin's Python `api` file even when this var is enabled — project plugins may extend the UI via static JS/CSS but their backend routes are only loaded when moved under `~/.hermes/plugins/`. |
| `HERMES_PLUGINS_DEBUG` | `1`/`true` to surface verbose plugin-discovery logs on stderr — directories scanned, manifests parsed, skip reasons, and full tracebacks on parse or `register()` failure. Aimed at plugin authors. |
| `HERMES_BACKGROUND_NOTIFICATIONS` | Background process notification mode in gateway: `concise` (default), `all`, `result`, `error`, `off` |
| `HERMES_EPHEMERAL_SYSTEM_PROMPT` | Ephemeral system prompt injected at API-call time (never persisted to sessions) |
| `HERMES_PREFILL_MESSAGES_FILE` | Path to a JSON file of ephemeral prefill messages injected at API-call time. |
| `HERMES_ALLOW_PRIVATE_URLS` | `true`/`false` — allow tools to fetch localhost/private-network URLs. Off by default in gateway mode. |
| `HERMES_REDACT_SECRETS` | `true`/`false` — control secret redaction in tool output, logs, and chat responses (default: `true`). |
| `HERMES_WRITE_SAFE_ROOT` | Optional directory prefix that **hard-blocks** `write_file`/`patch` writes outside the listed roots (no approval prompt). Supports multiple directories separated by `os.pathsep` (`:` on Unix, `;` on Windows). See [HERMES_WRITE_SAFE_ROOT](#hermes_write_safe_root) below. |
| `HERMES_DISABLE_LAZY_INSTALLS` | Internal PM policy used by tests and install probes. Truthy values refuse on-demand installation. It overrides the user-facing `security.allow_lazy_installs` setting. Do not put it in `.env`. |
| `HERMES_DISABLE_FILE_STATE_GUARD` | Set to `1` to turn off the "file changed since you read it" guard on `patch`/`write_file`. |
| `HERMES_BUNDLED_SKILLS` | Comma-separated override for the list of bundled skills loaded at startup. |
| `HERMES_OPTIONAL_SKILLS` | Comma-separated list of optional-skill names to auto-install on first run. |
| `HERMES_DEBUG_INTERRUPT` | Set to `1`/`true` to log detailed interrupt/cancel tracing to `agent.log`; `0`/`false`/`off` (or unset) keep it off. |
| `HERMES_DUMP_REQUESTS` | Dump API request payloads to log files (`true`/`false`) |
| `HERMES_DUMP_REQUEST_STDOUT` | Dump API request payloads to stdout instead of log files. |
| `HERMES_AGENT_HELP_GUIDANCE` | Append additional guidance text to the system prompt for custom deployments. |
| `HERMES_AGENT_LOGO` | Override the ASCII banner logo at CLI startup. |
| `DELEGATION_MAX_CONCURRENT_CHILDREN` | Max parallel subagents per `delegate_task` batch (default: `3`, floor of 1, no ceiling). Also configurable via `delegation.max_concurrent_children` in `config.yaml` — the config value takes priority. |

### HERMES_WRITE_SAFE_ROOT {#hermes_write_safe_root}

When this variable is set, `write_file` and `patch` may only target paths inside the listed directory prefix(es). Any path outside those roots is **rejected immediately** — the write does not go through the dangerous-command approval system and there is no prompt to override it.



**Do not add this to `~/.hermes/.env` unless you intend to sandbox writes.** A common mistake is pointing it at a project directory while expecting the agent to edit `~/.hermes/skills/` or scripts under a profile — those paths are outside the sandbox and every `write_file`/`patch` to them fails with an `outside HERMES_WRITE_SAFE_ROOT` error.

To allow both a workspace and Hermes state, list both prefixes (order does not matter):

```bash
export HERMES_WRITE_SAFE_ROOT=/path/to/project:/home/you/.hermes
```

Unset the variable or remove it from `.env` to restore normal writes (still subject to the credential-path denylist — see [File write safety](../user-guide/security.md#file-write-safety)).

### Internal bridge variables

Hermes sets these itself to carry state across a boundary where no `config.yaml` exists yet or where two processes need to agree. They are documented so you can recognise them in a process environment or a log; do not set them yourself, and never put them in `.env`.

| Variable | Description |
|----------|-------------|
| `HERMES_DATA_DIR_SUFFIX` | Baked into a desktop bundle's environment (`--bundle-env`, `HERMES_BUNDLE_ENV_JSON`, or channel builds with channel-specific data dirs, which use `-channel-build-<channel>`) so a test or channel build keeps its own data. It is appended literally to the default Hermes home and the default Electron `userData` directory, with no separator: `-channel-build-canary` selects `~/.hermes-channel-build-canary` on POSIX. Explicit `HERMES_HOME` and `HERMES_DESKTOP_USER_DATA_DIR` win and are not suffixed. It must be in the launch environment before startup, because it chooses the home that holds `.env` and `config.yaml`. |
| `HERMES_REPO_URL` | Git remote the installers (`scripts/install.sh`) clone from, and re-point `origin` to on a rerun. It is an environment variable because the installer runs before any Hermes config exists. Used by CI and rehearsal scripts to install from a fork or mirror; unset, the installers use the official repository. |
| `HERMES_UPDATE_STATUS_FILE` | Exported by the desktop update shim (`scripts/desktop-update/posix.sh`) with the path of the status JSON its progress window renders. The `hermes update` takeover children publish their long-running stages into that file so the window keeps moving. Absent (an older shim), they fall back to the status file named by the shim's pid in the update marker, and publish nothing when no UI is watching. |

## Interface

| Variable | Description |
|----------|-------------|
| `HERMES_TUI` | Launch the [TUI](../user-guide/tui.md) instead of the classic CLI when set to `1`. Equivalent to passing `--tui`. |
| `HERMES_TUI_DIR` | Path to a prebuilt `ui-tui/` directory (must contain `dist/entry.js` and populated `node_modules`). Used by distros and Nix to skip the first-launch `pnpm install`. |
| `HERMES_TUI_RESUME` | Resume a specific TUI session by ID on launch. When set, `hermes --tui` skips forging a fresh session and picks up the named session instead — useful for re-attaching after a disconnect or terminal crash. |
| `HERMES_TUI_THEME` | Force the TUI color theme: `light`, `dark`, or a raw 6-character background hex (e.g. `ffffff` or `1a1a2e`). When unset, Hermes auto-detects using `COLORFGBG` and terminal background queries; this variable overrides detection on terminals (Ghostty, Warp, iTerm2, etc.) that don't set `COLORFGBG`. |
| `HERMES_INFERENCE_MODEL` | Force the model for `hermes -z` / `hermes chat` without mutating `config.yaml`. Pairs with the `--provider` flag. Useful for scripted callers (sweeper, CI, batch runners) that need to override the default model per run. |

## Session Settings

| Variable | Description |
|----------|-------------|
| `HERMES_SESSION_ID` | **Exported automatically into every tool subprocess** Hermes spawns (`terminal`, `execute_code`, persistent shell, delegated subagent runs). Set by the agent to the current session ID; user scripts called from tools can read it to correlate their output, telemetry, or side effects with the originating Hermes session. **You should not set this manually** — overriding it from a parent shell only takes effect outside an agent run, and is overwritten the moment the agent starts a session. |
| `AI_AGENT` | **Set to `hermes-agent` by the CLI and gateway entry points** (only when not already set by an outer harness), and exported into terminal-tool shells. The emerging cross-agent standard for child-process attribution — generic tooling (e.g. huggingface_hub's agent detection) reads it to know it runs under an AI agent. The value matches Hermes' id in the public agent-harness registry. Don't set manually. |
| `HERMES_AGENT` | **Set to `true` by the CLI and gateway entry points** and exported into every terminal-tool shell so child processes can detect they run inside Hermes specifically. Don't set manually. |

Terminal session snapshots do not persist injected session/agent attribution
variables. Hermes supplies the current values for each command; an export
inside a previous terminal command does not redefine the next session identity.

## Context Compression (config.yaml only)

Context compression is configured exclusively through `config.yaml` — there are no environment variables for it. Threshold settings live in the `compression:` block, while the summarization model/provider lives under `auxiliary.compression:`.

```yaml
compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20         # fraction of threshold to preserve as recent tail
  protect_last_n: 20         # minimum recent messages to keep uncompressed
```

:::info Legacy migration
Older configs with `compression.summary_model`, `compression.summary_provider`, and `compression.summary_base_url` are automatically migrated to `auxiliary.compression.*` on first load.
:::

## Auxiliary Task Overrides

| Variable | Description |
|----------|-------------|
| `AUXILIARY_VISION_PROVIDER` | Override provider for vision tasks |
| `AUXILIARY_VISION_MODEL` | Override model for vision tasks |
| `AUXILIARY_VISION_BASE_URL` | Direct OpenAI-compatible endpoint for vision tasks |
| `AUXILIARY_VISION_API_KEY` | API key paired with `AUXILIARY_VISION_BASE_URL` |

:::note
`AUXILIARY_WEB_EXTRACT_*` variables are obsolete: `web_extract` no longer uses an auxiliary LLM. Long pages are truncated deterministically with the full text stored on disk for `read_file` paging.
:::

For task-specific direct endpoints, Hermes uses the task's configured API key or `OPENAI_API_KEY`. It does not reuse `OPENROUTER_API_KEY` for those custom endpoints.

## Fallback Providers (config.yaml only)

The primary model fallback chain is configured exclusively through `config.yaml` — there are no environment variables for it. Add a top-level `fallback_providers` list with `provider` and `model` keys to enable automatic failover when your main model encounters errors. Auxiliary tasks whose provider is `auto` also consult this chain before Hermes' built-in auxiliary discovery chain.

```yaml
fallback_providers:
  - provider: openrouter
    model: anthropic/claude-sonnet-4
```

The older top-level `fallback_model` single-provider shape is still read for backward compatibility, but new configuration should use `fallback_providers`. For task-specific auxiliary policy, use `auxiliary.<task>.fallback_chain` in `config.yaml`; there is no environment variable equivalent.

See [Fallback Providers](../user-guide/features/fallback-providers.md) for full details.

## Provider Routing (config.yaml only)

These go in `~/.hermes/config.yaml` under the `provider_routing` section:

| Key | Description |
|-----|-------------|
| `sort` | Sort providers: `"price"` (default), `"throughput"`, or `"latency"` |
| `only` | List of provider slugs to allow (e.g., `["anthropic", "google"]`) |
| `ignore` | List of provider slugs to skip |
| `order` | List of provider slugs to try in order |
| `require_parameters` | Only use providers supporting all request params (`true`/`false`) |
| `data_collection` | `"allow"` (default) or `"deny"` to exclude data-storing providers |

:::tip
Use `hermes config set` to set environment variables — every `UPPER_SNAKE` name on this page (and any other environment-shaped name) is saved to `.env`, the same file the setup flows write and the one the runtime reads; it is never written into `config.yaml`. Names on the env writer's denylist (`HERMES_HOME`, `HERMES_YOLO_MODE`, `PATH`, …) are refused. Dotted `config.yaml` settings go to `config.yaml`.
:::
