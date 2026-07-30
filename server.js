/**
 * voice-mcp-mossland - Node.js Server (Railway-ready)
 *
 * Mossland TTS engine via MCP Streamable HTTP protocol.
 * Runs on any Node.js 18+ host: Railway, Fly.io, etc.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";
import http from "node:http";

// =============================================================================
// HTML Audio Player (MCP ext-apps)
// =============================================================================

function getPlayerHTML(botName) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Voice Player</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: transparent; padding: 8px; }
    .container { background: #fff; border-radius: 16px; padding: 14px 16px; max-width: 100%; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
    .player { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
    .play-btn { width: 36px; height: 36px; border-radius: 50%; border: none; background: #f5f5f5; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .play-btn:hover { background: #eee; }
    .play-btn svg { width: 14px; height: 14px; fill: #333; }
    .play-btn.playing svg { fill: #07c160; }
    .waveform { flex: 1; display: flex; align-items: center; gap: 2px; height: 24px; }
    .wave-bar { width: 3px; background: #d0d0d0; border-radius: 2px; }
    .wave-bar.active { background: #07c160; }
    .duration { font-size: 13px; color: #999; min-width: 36px; text-align: right; }
    .toggle-btn { background: none; border: none; color: #07c160; font-size: 12px; cursor: pointer; padding: 8px 0 4px; display: flex; align-items: center; gap: 4px; }
    .toggle-btn:hover { text-decoration: underline; }
    .toggle-btn .arrow { display: inline-block; transition: transform 0.2s; font-size: 10px; }
    .toggle-btn.expanded .arrow { transform: rotate(90deg); }
    .text-bubble { background: #f7f7f7; border-radius: 8px; padding: 10px 12px; margin-top: 8px; font-size: 14px; line-height: 1.6; color: #333; display: none; }
    .text-bubble.show { display: block; }
    .loading { text-align: center; color: #999; font-size: 13px; padding: 16px; }
    .error { color: #fa5151; background: #fff2f2; padding: 10px; border-radius: 8px; font-size: 13px; }
    @media (prefers-color-scheme: dark) {
      .container { background: #2c2c2c; }
      .play-btn { background: #3a3a3a; }
      .play-btn svg { fill: #e0e0e0; }
      .wave-bar { background: #555; }
      .wave-bar.active { background: #4cd964; }
      .duration { color: #888; }
      .text-bubble { background: #3a3a3a; color: #e0e0e0; }
      .toggle-btn { color: #4cd964; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div id="content"><div class="loading">Loading...</div></div>
  </div>
  <script>
    const ce = document.getElementById('content');
    let audio = null, wi = null;
    function eh(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
    function sm(m) { ce.innerHTML = '<div class="error">' + eh(m) + '</div>'; }
    function ft(s) { const m = Math.floor(s/60), r = Math.floor(s%60); return m+':'+(r<10?'0':'')+r; }
    function cwf() { return [40,70,55,85,45,90,60,75,50,80,65,55,70,45,85,50].map(h => '<div class="wave-bar" style="height:'+h+'%"></div>').join(''); }
    function rp(t, b64) {
      const au = 'data:audio/mpeg;base64,'+b64;
      ce.innerHTML = '<div class="player"><button class="play-btn" id="pb"><svg viewBox="0 0 24 24"><path id="pi" d="M8 5v14l11-7z"/></svg></button><div class="waveform" id="wf">'+cwf()+'</div><span class="duration" id="du">0:00</span></div><button class="toggle-btn" id="tb"><span class="arrow">►</span> Show transcript</button><div class="text-bubble" id="tb2">'+eh(t)+'</div><audio id="au" src="'+au+'" preload="metadata"></audio>';
      const a = document.getElementById('au'), pb = document.getElementById('pb'), pi = document.getElementById('pi');
      const de = document.getElementById('du'), w = document.getElementById('wf'), ba = w.querySelectorAll('.wave-bar');
      const tx = document.getElementById('tb2'), tgl = document.getElementById('tb');
      a.addEventListener('loadedmetadata', () => de.textContent = ft(a.duration));
      pb.addEventListener('click', () => a.paused ? a.play() : a.pause());
      a.addEventListener('play', () => { pb.classList.add('playing'); pi.setAttribute('d','M6 19h4V5H6v14zm8-14v14h4V5h-4z'); aw(ba,true); });
      a.addEventListener('pause', () => { pb.classList.remove('playing'); pi.setAttribute('d','M8 5v14l11-7z'); aw(ba,false); });
      a.addEventListener('ended', () => { pb.classList.remove('playing'); pi.setAttribute('d','M8 5v14l11-7z'); aw(ba,false); ba.forEach(b=>b.classList.remove('active')); });
      a.addEventListener('timeupdate', () => { const p = a.currentTime / a.duration; const ac = Math.floor(p * ba.length); ba.forEach((b,i) => b.classList.toggle('active', i < ac)); });
      tgl.addEventListener('click', () => { const s = tx.classList.toggle('show'); tgl.classList.toggle('expanded',s); tgl.innerHTML = s ? '<span class="arrow">►</span> Hide transcript' : '<span class="arrow">►</span> Show transcript'; });
    }
    function aw(ba, p) { if (wi) clearInterval(wi); if (!p) return; wi = setInterval(() => ba.forEach(b => { if (!b.classList.contains('active')) b.style.opacity = 0.5 + Math.random()*0.5; }), 150); }
    function hd(d) { if (d.error) { sm(d.error); return; } if (d.audio_base64 && d.text) rp(d.text, d.audio_base64); }
    function sth(m, p, id) { const msg = { jsonrpc:'2.0', method:m, params:p||{} }; if(id!==undefined) msg.id = id; window.parent.postMessage(msg, '*'); }
    window.addEventListener('message', e => {
      const m = e.data; if (!m||typeof m!=='object') return;
      if (m.jsonrpc === '2.0') {
        if (m.method === 'ui/notifications/tool-input') ce.innerHTML = '<div class="loading">Generating voice...</div>';
        if (m.method === 'ui/notifications/tool-result') { const s = m.params?.structuredContent; if (s) hd(s); }
      }
      if (m.structuredContent) hd(m.structuredContent);
    });
    sth('ui/initialize',{name:'voice-mcp-mossland',version:'1.0.0'},1);
    setTimeout(() => sth('ui/notifications/initialized',{}), 50);
  </script>
</body></html>`;
}

// =============================================================================
// Mossland TTS (OpenAI-compatible)
// =============================================================================

const ENV = {
  MOSSLAND_API_KEY: process.env.MOSSLAND_API_KEY,
  MOSSLAND_VOICE_ID: process.env.MOSSLAND_VOICE_ID,
  MOSSLAND_BASE_URL: process.env.MOSSLAND_BASE_URL || 'https://api.mosi.cn/v1',
  MOSSLAND_TTS_MODEL: process.env.MOSSLAND_TTS_MODEL || 'moss-speech-turbo',
  BOT_NAME: process.env.BOT_NAME || 'S.CHI',
};

async function generateAudio(text) {
  try {
    const res = await fetch(`${ENV.MOSSLAND_BASE_URL}/audio/speech`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ENV.MOSSLAND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: ENV.MOSSLAND_TTS_MODEL,
        input: text,
        voice: ENV.MOSSLAND_VOICE_ID,
        response_format: 'mp3',
      }),
    });
    if (!res.ok) {
      const e = await res.text();
      try { return { success: false, error: JSON.parse(e).error?.message || e }; }
      catch { return { success: false, error: e || `HTTP ${res.status}` }; }
    }
    const buf = await res.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 8192) {
      bin += String.fromCharCode(...Array.from(bytes.slice(i, i + 8192)));
    }
    return { success: true, audio_base64: btoa(bin) };
  } catch (e) {
    return { success: false, error: e instanceof Error ? e.message : String(e) };
  }
}

// =============================================================================
// MCP Server Setup
// =============================================================================

const server = new McpServer({
  name: "voice-mcp-mossland",
  version: "1.0.0",
});

server.server.registerCapabilities({
  extensions: { "io.modelcontextprotocol/ui": {} },
});

const PLAYER_HTML = getPlayerHTML(ENV.BOT_NAME);
const RESOURCE_URI = "ui://voice-mcp/player.html";
const EXT_APPS_MIME = "text/html;profile=mcp-app";

server.resource(
  RESOURCE_URI, RESOURCE_URI,
  { mimeType: EXT_APPS_MIME, description: "Voice Player" },
  async () => ({
    contents: [{ uri: RESOURCE_URI, mimeType: EXT_APPS_MIME, text: PLAYER_HTML }],
  }),
);

server.registerTool(
  "speak",
  {
    title: `${ENV.BOT_NAME}'s Voice`,
    description: `Make ${ENV.BOT_NAME} speak with a custom cloned Mossland voice.`,
    inputSchema: z.object({ text: z.string().describe("Text to speak") }),
    _meta: { ui: { resourceUri: RESOURCE_URI }, "ui/resourceUri": RESOURCE_URI },
  },
  async ({ text }) => {
    const r = await generateAudio(text);
    if (r.success && r.audio_base64) {
      return {
        content: [{ type: "text", text: `🎙️ ${ENV.BOT_NAME} says: "${text}"` }],
        structuredContent: { text, audio_base64: r.audio_base64 },
      };
    }
    return {
      content: [{ type: "text", text: `Voice failed: ${r.error}` }],
      structuredContent: { error: r.error || 'Unknown' },
    };
  },
);

// =============================================================================
// HTTP Server
// =============================================================================

const transport = new WebStandardStreamableHTTPServerTransport({
  sessionIdGenerator: () => crypto.randomUUID(),
  enableJsonResponse: true,
});

await server.server.connect(transport);

const httpServer = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const path = url.pathname;

  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  // MCP endpoint
  if (path === '/mcp' || path === '/mcp/' || path === '/sse') {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = Buffer.concat(chunks);

      const webReq = new Request(`http://${req.headers.host}${req.url}`, {
        method: req.method,
        headers: Object.entries(req.headers).reduce((a, [k, v]) => {
          if (v) a[k] = Array.isArray(v) ? v.join(', ') : v;
          return a;
        }, {}),
        body: req.method === 'POST' && body.length > 0 ? body : undefined,
      });

      const webRes = await transport.handleRequest(webReq);
      res.writeHead(webRes.status, Object.fromEntries(webRes.headers));

      if (webRes.body) {
        const reader = webRes.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          res.write(value);
        }
      }
      res.end();
    } catch (err) {
      console.error('MCP error:', err);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Internal server error' }));
    }
    return;
  }

  // Direct audio API
  if (path === '/speak' && req.method === 'GET') {
    const text = url.searchParams.get('text');
    if (!text) { res.writeHead(400); res.end(JSON.stringify({ error: 'Missing text' })); return; }
    const r = await generateAudio(text);
    if (r.success && r.audio_base64) {
      const bs = atob(r.audio_base64);
      const b = new Uint8Array(bs.length);
      for (let i = 0; i < bs.length; i++) b[i] = bs.charCodeAt(i);
      res.writeHead(200, { 'Content-Type': 'audio/mpeg', 'Content-Disposition': 'inline' });
      res.end(Buffer.from(b));
    } else {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: r.error }));
    }
    return;
  }

  if (path === '/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', service: 'voice-mcp-mossland', engine: 'mossland', voice_id: ENV.MOSSLAND_VOICE_ID ? 'configured' : 'not configured' }));
    return;
  }

  // Landing
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>voice-mcp-mossland</title><style>body{font-family:system-ui;max-width:600px;margin:40px auto;padding:20px;color:#333;line-height:1.6}h1{color:#07c160}</style></head><body><h1>🎙️ voice-mcp-mossland</h1><p>MCP TTS server — Mossland engine</p><p>Bot: <strong>${ENV.BOT_NAME}</strong></p><p>MCP: <code>http://${req.headers.host}/mcp</code></p><p>Audio: <code>GET /speak?text=Hello</code></p><p>Status: <code>GET /status</code></p></body></html>`);
});

const PORT = process.env.PORT || 3000;
httpServer.listen(PORT, '0.0.0.0', () => {
  console.log(`voice-mcp-mossland running on port ${PORT}`);
});
