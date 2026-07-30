# 🎙️ voice-mcp-mossland

An MCP (Model Context Protocol) server for AI voice synthesis with an inline audio player. Uses **Mossland TTS** engine with your custom cloned voice.

> Forked from [garan0613/voice-mcp](https://github.com/garan0613/voice-mcp) — original MiniMax engine replaced with Mossland.

![License](https://img.shields.io/badge/license-MIT-green)

## Features

- 🎤 **Custom Voice Cloning** — Use Mossland TTS with your own cloned voice
- 🎵 **Inline Audio Player** — Beautiful WeChat-style player with waveform visualization
- 📝 **Transcript Toggle** — Show/hide the spoken text
- 🌙 **Dark Mode Support** — Automatic theme adaptation
- ⚡ **Railway Deployment** — Easy one-click deploy

## Quick Start (Local)

```bash
git clone https://github.com/zuohang20100323/voice-mcp-mossland.git
cd voice-mcp-mossland
npm install
```

Set environment variables:

```bash
export MOSSLAND_API_KEY=your-key
export MOSSLAND_VOICE_ID=your-voice-id
npm start
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `MOSSLAND_API_KEY` | ✅ | Your Mossland API key |
| `MOSSLAND_VOICE_ID` | ✅ | The cloned voice ID |
| `MOSSLAND_BASE_URL` | ❌ | Mossland API base URL (default: `https://api.mosi.cn/v1`) |
| `MOSSLAND_TTS_MODEL` | ❌ | TTS model name (default: `moss-speech-turbo`) |
| `BOT_NAME` | ❌ | Display name (default: "S.CHI") |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /mcp` | MCP Streamable HTTP endpoint |
| `GET /speak?text=Hello` | Direct audio download |
| `GET /status` | Health check |

## License

MIT © 2026
