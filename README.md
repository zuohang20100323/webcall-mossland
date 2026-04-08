# WebCall — 开源 AI 实时语音通话

一个可自部署的「网页端实时语音通话」应用：用户像打电话一样与 AI 进行语音交流，支持自定义角色人设、多种大模型/语音能力配置，并提供基础的用户与用量管理。

首次部署通过网页向导完成所有配置，无需手动编辑环境变量。

![截图](screenshots/demo.png)

## 功能

- 实时语音通话（Web 前端 + 后端接口）
- 多 LLM 支持（OpenAI 兼容 API 地址/Key/模型名可配置）
- 多 TTS 支持（MiniMax TTS，可自带账号或使用站内 Key 计费模式）
- STT 语音识别（支持智谱 GLM-ASR / Groq Whisper 等降级方案，按配置启用）
- 用户系统（口令体验 / 账号登录）
- 按量计费与余额（TTS API Key 模式）
- 管理面板（配置与数据管理）

## 快速部署

### 方式 A：HuggingFace Spaces

1. 新建 Space（推荐 Docker Space）。
2. 将本仓库代码上传到 Space。
3. 在 Space 的 Secrets / Variables 中配置必要环境变量（见下方“配置说明”），或使用首次配置向导写入 `config.json`。
4. 启动后访问 Space URL，按页面提示完成初始化。

### 方式 B：Docker

```bash
git clone <your-fork-or-this-repo>
cd voice-call-open

# 可选：复制并修改环境变量
cp .env.example .env

docker build -t ai-voice-call .
docker run --rm -p 7860:7860 --env-file .env ai-voice-call
```

启动后打开：`http://localhost:7860`。

## 配置说明

本项目支持两种配置方式：

1. **首次配置向导**：首次启动若未完成配置，会自动跳转到 `/setup-wizard`，保存到本地 `config.json`。
2. **环境变量**：也可通过 `.env` / 系统环境变量提供配置（`config.json` 优先）。

常用配置项（示例见 `.env.example`）：

- LLM
  - `LLM_API_BASE`：OpenAI 兼容 API Base（如 `https://api.openai.com/v1`）
  - `LLM_API_KEY`：API Key
  - `LLM_MODEL`：模型名（可选）
- TTS（MiniMax）
  - `MINIMAX_API_KEY`
  - `MINIMAX_GROUP_ID`
  - `MINIMAX_VOICE_ID`（可选）
- STT
  - `ZHIPU_API_KEY`（可选）
  - `GROQ_API_KEY`（可选）
- 管理
  - `ADMIN_PASSWORD`
- 角色
  - `CHARACTER_NAME`：页面与部分提示词中使用的角色名称（默认“AI助手”）

## 技术架构

- 后端：Flask（HTTP API）
- 通话：WebSocket/流式接口驱动的实时对话
- 前端：移动端风格 UI（iOS / Y2K 两套主题模板）
- 配置：`config.json`（首次向导写入）+ 环境变量（fallback）

## 许可证

本项目使用 **AGPL-3.0** 协议开源，详见 [LICENSE](./LICENSE)。
