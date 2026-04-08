// === 主题系统 ===
function getTheme() {
    try { return localStorage.getItem('ui_theme') || 'ios'; } catch(e) { return 'ios'; }
}
function setTheme(name) {
    fetch('/api/set-theme', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({theme: name})
    }).then(function() {
        try { localStorage.setItem('ui_theme', name); } catch(e) {}
        window.location.reload();
    });
}
// 页面加载时读取当前主题（不刷新，仅用于高亮按钮）
(function() {
    var _t = getTheme();
    // 高亮当前主题按钮
    document.querySelectorAll('.theme-btn').forEach(function(btn) {
        var isActive = btn.getAttribute('data-theme-val') === _t;
        btn.style.background = isActive ? '#0A84FF' : '';
        btn.style.fontWeight = isActive ? '600' : '400';
    });
})();

// === 日志系统 ===
var _logLines = [];
function _ts() { return new Date().toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
function addLog(level, msg) {
    var line = _ts() + ' [' + level + '] ' + msg;
    _logLines.push(line);
    if (_logLines.length > 500) _logLines = _logLines.slice(-300);
    var el = document.getElementById('logContent');
    if (el) {
        var cls = 'log-' + level.toLowerCase();
        el.innerHTML += '<div class="log-line ' + cls + '"><span class="log-time">' + _ts() + '</span> ' + msg + '</div>';
        el.scrollTop = el.scrollHeight;
    }
}
function copyLog() {
    var text = _logLines.join('\n');
    navigator.clipboard.writeText(text).then(function(){
        addLog('INFO', '日志已复制到剪贴板');
    }).catch(function(){});
}
function clearLog() {
    _logLines = [];
    document.getElementById('logContent').innerHTML = '';
}

// === 环境检测 ===
addLog('INFO', '页面加载 | UA: ' + navigator.userAgent.slice(0, 80));
addLog('INFO', '浏览器: ' + (function(){
    var ua = navigator.userAgent;
    if (ua.indexOf('MicroMessenger') > -1) return '微信内置浏览器 ⚠️';
    if (ua.indexOf('QQBrowser') > -1) return 'QQ浏览器 ⚠️';
    if (ua.indexOf('UCBrowser') > -1) return 'UC浏览器 ⚠️';
    if (ua.indexOf('Quark') > -1) return '夸克浏览器 ⚠️';
    if (ua.indexOf('Firefox') > -1) return 'Firefox ✅';
    if (ua.indexOf('Edg/') > -1) return 'Edge ✅';
    if (ua.indexOf('Chrome') > -1) return 'Chrome ✅';
    if (ua.indexOf('Safari') > -1) return 'Safari ✅';
    return '未知 ⚠️';
})());
addLog('INFO', 'MediaRecorder: ' + (typeof MediaRecorder !== 'undefined' ? '✅ 支持' : '❌ 不支持'));
addLog('INFO', 'getUserMedia: ' + (navigator.mediaDevices && navigator.mediaDevices.getUserMedia ? '✅ 支持' : '❌ 不支持'));
addLog('INFO', 'AudioContext: ' + (window.AudioContext || window.webkitAudioContext ? '✅ 支持' : '❌ 不支持'));
if (typeof MediaRecorder !== 'undefined') {
    var _codecs = ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg'];
    var _supported = _codecs.filter(function(c){return MediaRecorder.isTypeSupported(c)});
    addLog('INFO', '录音格式: ' + (_supported.length ? _supported.join(', ') : '❌ 无支持格式'));
}

// === 初始化 ===
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';
if (!TOKEN) { window.location.href = '/'; }
const sessionId = 'vc_' + Date.now();

// 从 URL 参数读取设置（HF Spaces iframe 中 localStorage 被阻止）
const _params = new URLSearchParams(window.location.search);

// 优先从 sessionStorage 读（安全，不暴露在 URL 中）
var _callData = {};
try { var _cd = sessionStorage.getItem('call_data'); if (_cd) _callData = JSON.parse(_cd); } catch(e) {}

let userInfo = _callData.user_info || _params.get('user_info') || '';
let topic = _callData.topic || _params.get('topic') || '';
let extraPrompt = _callData.extra_prompt || _params.get('extra_prompt') || '';
let userId = _callData.user_id || _params.get('user_id') || '';
// localStorage fallback（持久化设定）
try {
    if (!userId) userId = localStorage.getItem('vc_userId') || '';
    if (!userInfo) userInfo = localStorage.getItem('vc_userInfo') || '';
    if (!topic) topic = localStorage.getItem('vc_topic') || '';
    if (!extraPrompt) extraPrompt = localStorage.getItem('vc_extraPrompt') || '';
} catch(e) {}
let customLlm = null;
try {
  var _clRaw = _callData.custom_llm || _params.get('custom_llm');
  if (_clRaw) customLlm = typeof _clRaw === 'string' ? JSON.parse(_clRaw) : _clRaw;
} catch(e) {}
let customTts = null;
try {
  var _ctRaw = _callData.custom_tts || _params.get('custom_tts');
  if (_ctRaw) customTts = typeof _ctRaw === 'string' ? JSON.parse(_ctRaw) : _ctRaw;
} catch(e) {}
let ttsApiKey = _callData.tts_api_key || _params.get('tts_api_key') || '';
let customVision = null; // 独立识图模型 {base_url, api_key, model}
try {
  var _cvRaw = localStorage.getItem('vc_customVision');
  if (_cvRaw) customVision = JSON.parse(_cvRaw);
} catch(e) {}
let selectedModel = _callData.model || _params.get('model') || '';
let maxHistory = parseInt(_callData.max_history || _params.get('max_history') || '20') || 20;
let filterRules = null;
try {
  var _frRaw = _callData.filter_rules;
  if (_frRaw) filterRules = typeof _frRaw === 'string' ? JSON.parse(_frRaw) : _frRaw;
} catch(e) {}

// 构建 custom_prompt
let customPrompt = '';
if (userId) customPrompt += '# 用户称呼\n' + userId + '\n\n';
if (userInfo) customPrompt += '# 关于用户\n' + userInfo + '\n\n';
if (topic) customPrompt += '# 本次话题\n' + topic + '\n\n';
if (extraPrompt) customPrompt += '# 补充设定\n' + extraPrompt + '\n\n';

// === 状态变量 ===
let showSubtitle = true; // 是否显示通话字幕
try { showSubtitle = localStorage.getItem('show_subtitle') !== 'false'; } catch(e) {}
let apiRetryCount = 3; // API报错重试次数
try { var _rc = localStorage.getItem('api_retry_count'); if (_rc) apiRetryCount = parseInt(_rc) || 3; } catch(e) {}

// === 视频相关状态 ===
let _videoEnabled = false;
try { _videoEnabled = localStorage.getItem('video_enabled') === 'true'; } catch(e) {}
let _videoStream = null;       // 视频专用 mediaStream（不影响音频录音流）
let _latestVideoFrame = '';    // 最新截图 base64 JPEG（不含 data: 前缀）
let _videoCaptureTimer = null; // 截图定时器
let _videoFullscreen = false;  // 视频是否全屏显示
let _facingMode = 'user';      // 摄像头方向: 'user'=前置, 'environment'=后置
let audioSegments = []; // [{type:'user'|'ai', audio_b64:string, format:string, timestamp:number, msg_index?:number}]
let _assistantMsgCount = 0; // 当前通话中 assistant 消息计数，用于音频-消息精确对应
let historyCount = 0; // 加载最近N次通话总结作为上下文
let isInCall = false;
let isMuted = false;
let isAiSpeaking = false;
let isPlaying = false;
let isListening = false;
let audioQueue = [];
let currentAiText = '';
let callStartTime = null;
let timerInterval = null;
let mediaRecorder = null;
let audioChunks = [];
let mediaStream = null;
let audioContext = null;
let analyser = null;
let silenceTimer = null;
let silenceStart = null;
let hasVoiceActivity = false;
let voiceFrameCount = 0;
let recordStartTime = 0;
const SILENCE_THRESHOLD = 15;
const SILENCE_DURATION = 1500;
const MIN_RECORD_TIME = 500;

// === 视图切换 ===
function switchView(name) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view' + name.charAt(0).toUpperCase() + name.slice(1)).classList.add('active');
    if (name === 'call') {
        document.getElementById('tabBar').style.display = 'none';
    }
}

function switchTab(name) {
    document.querySelectorAll('.view').forEach(function(v) { v.classList.remove('active'); });
    document.querySelectorAll('.tab-item').forEach(function(t) { t.classList.remove('active'); });

    if (name === 'recents') {
        document.getElementById('viewRecents').classList.add('active');
        document.querySelectorAll('.tab-item')[0].classList.add('active');
        document.getElementById('tabBar').style.display = 'flex';
        loadCallHistory();
    } else if (name === 'dial') {
        document.getElementById('viewDial').classList.add('active');
        document.querySelectorAll('.tab-item')[1].classList.add('active');
        document.getElementById('tabBar').style.display = 'flex';
    } else if (name === 'profile') {
        document.getElementById('viewProfile').classList.add('active');
        document.querySelectorAll('.tab-item')[2].classList.add('active');
        document.getElementById('tabBar').style.display = 'flex';
        updateProfileOverview();
    }
}

function updateProfileOverview() {
    // 加载余额（仅账号用户）
    loadProfileBalance();

    fetch('/api/call-history?token=' + TOKEN)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var allCalls = data.calls || [];
            // 统计
            var countEl = document.getElementById('profileCallCount');
            var durationEl = document.getElementById('profileTotalDuration');
            if (countEl) countEl.textContent = allCalls.length + ' 次';
            if (durationEl) {
                var totalSec = allCalls.reduce(function(sum, c) { return sum + (c.duration || 0); }, 0);
                if (totalSec > 3600) {
                    durationEl.textContent = Math.floor(totalSec/3600) + '小时' + Math.floor((totalSec%3600)/60) + '分';
                } else if (totalSec > 60) {
                    durationEl.textContent = Math.floor(totalSec/60) + '分' + (totalSec%60) + '秒';
                } else {
                    durationEl.textContent = totalSec + '秒';
                }
            }
            // 记忆预览
            var memCount = allCalls.filter(function(c) { return c.memory || c.summary; }).length;
            var previewEl = document.getElementById('memoryPreview');
            if (previewEl) previewEl.textContent = memCount > 0 ? memCount + ' 条记忆' : '暂无记忆';
        })
        .catch(function() {});
}

// === 余额与卡密充值 ===
function loadProfileBalance() {
    var balanceEl = document.getElementById('profileBalance');
    var balanceCard = document.getElementById('balanceCard');
    if (!balanceEl || !balanceCard) return;

    // 检查是否是账号用户
    var isAccountUser = false;
    try { isAccountUser = !!localStorage.getItem('account_token'); } catch(e) {}
    if (!isAccountUser) {
        balanceCard.style.display = 'none';
        return;
    }
    balanceCard.style.display = '';

    fetch('/api/account/balance?token=' + TOKEN)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                balanceEl.textContent = '获取失败';
                return;
            }
            balanceEl.textContent = '¥' + (data.balance_yuan || 0).toFixed(2);
        })
        .catch(function() {
            balanceEl.textContent = '加载失败';
        });
}

function openRedeemCardModal() {
    var modal = document.getElementById('redeemCardModal');
    if (modal) {
        modal.style.display = 'flex';
        document.getElementById('redeemCardInput').value = '';
        document.getElementById('redeemCardMsg').textContent = '';
        document.getElementById('redeemCardMsg').style.color = '#8e8e93';
    }
    // 检查充值窗口状态，显示二维码
    fetch('/api/recharge/status')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var area = document.getElementById('rechargeQrArea');
            if (data.open && area) {
                area.style.display = 'block';
                if (data.qr_url) document.getElementById('rechargeQrImg').src = data.qr_url;
                if (data.note) document.getElementById('rechargeQrNote').textContent = data.note;
            } else if (area) {
                area.style.display = 'none';
            }
        })
        .catch(function() {});
}

function closeRedeemCardModal() {
    var modal = document.getElementById('redeemCardModal');
    if (modal) modal.style.display = 'none';
}

function doRedeemCard() {
    var input = document.getElementById('redeemCardInput');
    var msg = document.getElementById('redeemCardMsg');
    var btn = document.getElementById('redeemCardBtn');
    var code = (input.value || '').trim();
    if (!code) {
        msg.textContent = '请输入卡密';
        msg.style.color = '#FF3B30';
        return;
    }
    btn.disabled = true;
    btn.textContent = '充值中...';
    msg.textContent = '';

    fetch('/api/account/redeem-card?token=' + TOKEN, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_code: code })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            msg.textContent = data.error;
            msg.style.color = '#FF3B30';
        } else {
            msg.textContent = '充值成功！+¥' + (data.amount_yuan || 0) + '，余额 ¥' + (data.balance_yuan || 0);
            msg.style.color = '#34C759';
            input.value = '';
            // 刷新余额
            loadProfileBalance();
        }
    })
    .catch(function() {
        msg.textContent = '网络错误，请重试';
        msg.style.color = '#FF3B30';
    })
    .finally(function() {
        btn.disabled = false;
        btn.textContent = '确认充值';
    });
}// === cleanVoiceTags ===
function cleanVoiceTags(text) {
    // 只过滤已知的 MiniMax 语气词标签
    let c = text.replace(/\((laughs|chuckle|coughs|clear-throat|groans|breath|pant|inhale|exhale|gasps|sniffs|sighs|snorts|burps|lip-smacking|humming|hissing|emm|whistles|sneezes|crying|applause)\)/gi, '');
    // 过滤完整的停顿标签 <#0.5#> <#1> 等
    c = c.replace(/<#[\d.]+#?>/g, '');
    // 清理字符串末尾未闭合的停顿标签残留（流式截断：<#0.5# 或 <#0.5 或 <#）
    c = c.replace(/<#[\d.#]*$/, '');
    return c.replace(/\s{2,}/g, ' ').trim();
}

// === 视频功能 ===

function toggleVideo() {
    if (_videoEnabled) {
        stopVideo();
    } else {
        startVideo();
    }
}

function startVideo() {
    if (_videoStream) return; // 已经在运行
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        addLog('ERROR', '浏览器不支持摄像头');
        return;
    }
    addLog('INFO', '请求摄像头权限...');
    navigator.mediaDevices.getUserMedia({
        video: { facingMode: _facingMode, width: { ideal: 640 }, height: { ideal: 480 } }
    }).then(function(stream) {
        _videoStream = stream;
        _videoEnabled = true;
        try { localStorage.setItem('video_enabled', 'true'); } catch(e) {}
        addLog('INFO', '✅ 摄像头已开启');

        // 将视频流连接到 video 元素
        var videoEl = document.getElementById('videoPreview');
        if (videoEl) {
            videoEl.srcObject = stream;
            videoEl.style.transform = (_facingMode === 'user') ? 'scaleX(-1)' : '';
            videoEl.play().catch(function(){});
        }

        // 显示视频容器
        var container = document.getElementById('videoContainer');
        if (container) container.style.display = 'block';

        // 更新按钮状态
        var btn = document.getElementById('btnVideo');
        if (btn) btn.classList.add('active');

        // 开始截图
        startVideoCapture();
    }).catch(function(e) {
        addLog('ERROR', '摄像头权限被拒绝: ' + e.message);
        _videoEnabled = false;
    });
}

function stopVideo() {
    _videoEnabled = false;
    try { localStorage.setItem('video_enabled', 'false'); } catch(e) {}

    // 停止截图
    stopVideoCapture();

    // 停止视频流
    if (_videoStream) {
        _videoStream.getTracks().forEach(function(t) { t.stop(); });
        _videoStream = null;
    }

    // 清理 video 元素
    var videoEl = document.getElementById('videoPreview');
    if (videoEl) {
        videoEl.srcObject = null;
    }

    // 隐藏视频容器
    var container = document.getElementById('videoContainer');
    if (container) container.style.display = 'none';

    // 更新按钮状态
    var btn = document.getElementById('btnVideo');
    if (btn) btn.classList.remove('active');

    // 清空截图（但不清空 _latestVideoFrame，下次发送还能用最后一帧）
    _videoFullscreen = false;
    var container2 = document.getElementById('videoContainer');
    if (container2) {
        container2.classList.remove('fullscreen');
    }

    addLog('INFO', '📹 摄像头已关闭');
}

function startVideoCapture() {
    stopVideoCapture(); // 先清理
    _videoCaptureTimer = setInterval(captureVideoFrame, 1500); // 每 1.5 秒截一帧
    addLog('DEBUG', '视频截图开始 (1.5s/帧)');
}

function stopVideoCapture() {
    if (_videoCaptureTimer) {
        clearInterval(_videoCaptureTimer);
        _videoCaptureTimer = null;
    }
}

function captureVideoFrame() {
    var videoEl = document.getElementById('videoPreview');
    if (!videoEl || !videoEl.srcObject || videoEl.videoWidth === 0) return;

    var canvas = document.getElementById('videoCanvas');
    if (!canvas) return;

    // 缩放到宽 512px，保持比例
    var targetWidth = 512;
    var scale = targetWidth / videoEl.videoWidth;
    var targetHeight = Math.round(videoEl.videoHeight * scale);

    canvas.width = targetWidth;
    canvas.height = targetHeight;

    var ctx = canvas.getContext('2d');
    // 截图始终用原始方向（不镜像），确保文字可识别
    ctx.drawImage(videoEl, 0, 0, targetWidth, targetHeight);

    // 导出为 JPEG base64，quality 0.6
    var dataUrl = canvas.toDataURL('image/jpeg', 0.6);
    // 去掉 data:image/jpeg;base64, 前缀
    _latestVideoFrame = dataUrl.split(',')[1] || '';
}

function toggleVideoSize() {
    _videoFullscreen = !_videoFullscreen;
    var container = document.getElementById('videoContainer');
    if (container) {
        container.classList.toggle('fullscreen', _videoFullscreen);
    }
}

function switchCamera() {
    if (!_videoEnabled) return;
    _facingMode = (_facingMode === 'user') ? 'environment' : 'user';
    addLog('INFO', '切换摄像头: ' + (_facingMode === 'user' ? '前置' : '后置'));
    // 停掉当前流，重新打开
    if (_videoStream) {
        _videoStream.getTracks().forEach(function(t) { t.stop(); });
        _videoStream = null;
    }
    stopVideoCapture();
    navigator.mediaDevices.getUserMedia({
        video: { facingMode: _facingMode, width: { ideal: 640 }, height: { ideal: 480 } }
    }).then(function(stream) {
        _videoStream = stream;
        var videoEl = document.getElementById('videoPreview');
        if (videoEl) {
            videoEl.srcObject = stream;
            videoEl.style.transform = (_facingMode === 'user') ? 'scaleX(-1)' : '';
            videoEl.play().catch(function(){});
        }
        startVideoCapture();
    }).catch(function(e) {
        addLog('ERROR', '切换摄像头失败: ' + e.message);
    });
}

// === SSE 发送 ===
function sendText(text) {
    addLog('INFO', '发送文本: ' + text.slice(0, 50));
    const body = { text, session_id: sessionId, token: TOKEN, timestamp: new Date().toLocaleString('zh-CN', {hour12: false}) };
    if (customPrompt) body.custom_prompt = customPrompt;
    if (selectedModel) body.model = selectedModel;
    if (maxHistory) body.max_history = maxHistory;
    if (customLlm && customLlm.base_url && customLlm.api_key) body.custom_api = customLlm;
    if (customTts && customTts.api_key) body.custom_tts = customTts;
    if (ttsApiKey) body.tts_api_key = ttsApiKey;
    if (filterRules && filterRules.length > 0) body.filter_rules = filterRules;
    if (historyCount > 0) body.history_count = historyCount;
    // 附加视频截图
    if (_latestVideoFrame) {
        body.image = _latestVideoFrame;
        addLog('DEBUG', '附加视频截图 (' + Math.round(_latestVideoFrame.length / 1024) + 'KB)');
        // 独立识图模型
        if (customVision && customVision.base_url && customVision.api_key) {
            body.vision_api = customVision;
        }
    }

    var _retryLeft = apiRetryCount;
    function _doFetch() {
    fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).then(resp => {
        addLog('INFO', 'SSE 响应: HTTP ' + resp.status);
        if (resp.status >= 500 || resp.status === 429) {
            _retryLeft--;
            if (_retryLeft > 0) {
                addLog('WARN', 'API错误 ' + resp.status + '，重试中... 剩余 ' + _retryLeft + ' 次');
                document.getElementById('statusLine').textContent = 'API错误(' + resp.status + ')，重试中...';
                setTimeout(_doFetch, 2000);
                return;
            }
        }
        if (resp.status === 401) {
            resp.json().then(data => {
                alert(data.error || '会话已过期，请重新登录');
                window.location.href = '/';
            }).catch(() => {
                alert('会话已过期，请重新登录');
                window.location.href = '/';
            });
            return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        function pump() {
            reader.read().then(({ done, value }) => {
                if (done) return;
                buf += decoder.decode(value, { stream: true });
                const lines = buf.split('\n');
                buf = lines.pop();
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') return;
                        try { handleMessage(JSON.parse(data)); } catch(e) {}
                    }
                }
                pump();
            });
        }
        pump();
    }).catch(err => {
        console.error('SSE error:', err);
        addLog('ERROR', 'SSE 网络错误: ' + err.message);
        _retryLeft--;
        if (_retryLeft > 0) {
            addLog('WARN', '重试中... 剩余 ' + _retryLeft + ' 次');
            document.getElementById('statusLine').textContent = '网络错误，重试中... (' + (apiRetryCount - _retryLeft) + '/' + apiRetryCount + ')';
            setTimeout(_doFetch, 1500);
        } else {
            document.getElementById('aiSubtitle').textContent = '⚠️ 网络连接失败';
            document.getElementById('statusLine').textContent = '已重试' + apiRetryCount + '次，全部失败';
            isAiSpeaking = false;
            isPlaying = false;
            resumeListening();
        }
    });
    }
    _doFetch();
}

// === 消息处理 ===
function handleMessage(msg) {
    switch (msg.type) {
        case 'user_confirmed':
            addLog('INFO', '✅ 服务端已收到文本');
            if (showSubtitle) document.getElementById('userSubtitle').textContent = '你: ' + msg.text;
            document.getElementById('statusLine').textContent = '已收到，正在处理...';
            isAiSpeaking = true;
            pauseListening();
            break;
        case 'status':
            document.getElementById('statusLine').textContent = msg.message || '';
            break;
        case 'vision_log':
            addLog('INFO', '🎥 ' + msg.text);
            document.getElementById('statusLine').textContent = msg.text;
            break;
        case 'text_delta':
            currentAiText += msg.text;
            if (showSubtitle) {
                document.getElementById('aiSubtitle').textContent = cleanVoiceTags(currentAiText);
            }
            // 自动滚到底部，确保最新文字可见
            var subtitleArea = document.querySelector('.subtitle-area');
            if (subtitleArea) subtitleArea.scrollTop = subtitleArea.scrollHeight;
            break;
        case 'audio':
            addLog('DEBUG', 'TTS 音频 #' + msg.index + ' (' + (msg.audio||'').length + ' bytes)');
            audioSegments.push({type:'ai', audio_b64:msg.audio, format:'mp3', timestamp:Date.now(), msg_index:_assistantMsgCount});
            audioQueue.push({ audio: msg.audio, text: msg.text, index: msg.index });
            playNext();
            break;
        case 'done':
            addLog('INFO', '✅ LLM 完成 | ' + (msg.stats ? msg.stats.sentences + '句TTS' : ''));
            document.getElementById('statusLine').textContent = '';
            if (msg.stats) {
                let s = msg.stats;
                let info = '总耗时 ' + s.total_time + 's';
                if (s.first_token_time) info += ' | 首token ' + s.first_token_time + 's';
                info += ' | LLM ' + s.llm_time + 's | ' + s.sentences + '句TTS';
                document.getElementById('statsLine').textContent = info;
            }
            if (!isPlaying && audioQueue.length === 0) { isAiSpeaking = false; resumeListening(); }
            _assistantMsgCount++;
            break;
        case 'tts_error':
            addLog('ERROR', 'TTS 失败: ' + msg.text);
            document.getElementById('statusLine').textContent = '⚠️ 语音合成失败: ' + msg.text;
            // 如果是余额不足，显示更醒目的提示
            if (msg.text && (msg.text.indexOf('余额') >= 0 || msg.text.indexOf('balance') >= 0)) {
                document.getElementById('aiSubtitle').textContent = '⚠️ TTS余额不足，请充值后继续';
            }
            break;
        case 'error':
            addLog('ERROR', '服务端错误: ' + msg.message);
            document.getElementById('aiSubtitle').textContent = '⚠️ ' + msg.message;
            document.getElementById('statusLine').textContent = '';
            isAiSpeaking = false;
            resumeListening();
            break;
    }
}

// === 音频播放队列 ===
function playNext() {
    if (isPlaying || audioQueue.length === 0) return;
    isPlaying = true;
    const item = audioQueue.shift();
    addLog('DEBUG', '播放音频 #' + item.index);
    const bytes = Uint8Array.from(atob(item.audio), c => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: 'audio/mpeg' });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.volume = 1.0;
    pauseListening();

    var playDone = false;
    function onDone() {
        if (playDone) return;
        playDone = true;
        if (audio.error) {
            var errDetail = audio.error.message || ('code=' + audio.error.code);
            addLog('WARN', '音频播放出错: ' + errDetail);
            document.getElementById('statusLine').textContent = '⚠️ 音频播放失败: ' + errDetail;
        }
        URL.revokeObjectURL(url);
        isPlaying = false;
        if (audioQueue.length > 0) { playNext(); }
        else { isAiSpeaking = false; resumeListening(); }
    }
    audio.onended = audio.onerror = onDone;

    // 先尝试 Audio 元素播放
    audio.play().catch((e) => {
        addLog('WARN', '音频 play() 失败，尝试 AudioContext 播放: ' + e.message);
        document.getElementById('statusLine').textContent = '⚠️ 自动播放被阻止，尝试备用方式...';
        // fallback: 用 AudioContext 解码播放（不受自动播放限制）
        try {
            var ctx = _audioContext || new (window.AudioContext || window.webkitAudioContext)();
            _audioContext = ctx;
            if (ctx.state === 'suspended') ctx.resume();
            var arrayBuf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
            ctx.decodeAudioData(arrayBuf.slice(0), function(decoded) {
                var src = ctx.createBufferSource();
                src.buffer = decoded;
                src.connect(ctx.destination);
                src.onended = onDone;
                src.start(0);
                addLog('INFO', '✅ AudioContext 播放成功');
                document.getElementById('statusLine').textContent = '';
            }, function(decErr) {
                addLog('ERROR', 'AudioContext 解码失败: ' + decErr);
                document.getElementById('statusLine').textContent = '⚠️ 音频解码失败，请刷新页面重试';
                onDone();
            });
        } catch(ctxErr) {
            addLog('ERROR', 'AudioContext 创建失败: ' + ctxErr.message);
            document.getElementById('statusLine').textContent = '⚠️ 音频播放器初始化失败: ' + ctxErr.message;
            onDone();
        }
    });
}

// === 录音 + VAD ===
function initRecognition() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        addLog('ERROR', '浏览器不支持 getUserMedia');
        document.getElementById('hint').textContent = '⚠️ 浏览器不支持录音';
        return false;
    }
    addLog('INFO', '请求麦克风权限...');
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        addLog('INFO', '✅ 麦克风权限已获取');
        mediaStream = stream;
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioContext.createMediaStreamSource(stream);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        document.getElementById('hint').textContent = '🎤 麦克风就绪';
        startRecording();
    }).catch((e) => {
        addLog('ERROR', '麦克风权限被拒绝: ' + e.message);
        document.getElementById('hint').textContent = '⚠️ 麦克风权限被拒绝，请打字输入';
    });
    return true;
}

function startRecording() {
    if (!mediaStream || !isInCall || isMuted || isAiSpeaking) return;
    audioChunks = [];
    recordStartTime = Date.now();
    hasVoiceActivity = false;
    voiceFrameCount = 0;
    let mimeType = 'audio/webm;codecs=opus';
    if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'audio/webm';
    if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'audio/mp4';
    if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = '';
    try {
        mediaRecorder = new MediaRecorder(mediaStream, mimeType ? { mimeType } : {});
    } catch(e) { return; }
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = () => {
        const dur = Date.now() - recordStartTime;
        if (dur < MIN_RECORD_TIME || audioChunks.length === 0 || !hasVoiceActivity) {
            audioChunks = [];
            if (isInCall && !isMuted && !isPlaying && !isAiSpeaking) setTimeout(startRecording, 200);
            return;
        }
        const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
        audioChunks = [];
        if (blob.size < 1000) { if (isInCall && !isMuted && !isPlaying) setTimeout(startRecording, 200); return; }
        // 收集用户录音到 audioSegments
        var reader2 = new FileReader();
        reader2.onloadend = function() {
            var b64 = reader2.result.split(',')[1]; // 去掉 data:xxx;base64, 前缀
            audioSegments.push({type:'user', audio_b64:b64, format:'webm', timestamp:Date.now()});
        };
        reader2.readAsDataURL(blob);
        document.getElementById('userSubtitle').textContent = '🎤 识别中...';
        document.getElementById('statusLine').textContent = '语音识别中...';
        addLog('INFO', '发送录音到 STT (' + blob.size + ' bytes)');
        const fd = new FormData();
        fd.append('audio', blob, 'recording.webm');
        fetch('/api/recognize', { method: 'POST', body: fd }).then(r => r.json()).then(result => {
            if (result.error) { addLog('ERROR', 'STT 错误: ' + result.error); document.getElementById('statusLine').textContent = '⚠️ ' + result.error; if (isInCall && !isMuted && !isPlaying) setTimeout(startRecording, 500); return; }
            const text = (result.text || '').trim();
            if (!text) { addLog('DEBUG', 'STT 返回空（静音）'); document.getElementById('userSubtitle').textContent = ''; document.getElementById('statusLine').textContent = ''; if (isInCall && !isMuted && !isPlaying) setTimeout(startRecording, 200); return; }
            addLog('INFO', 'STT 识别: 「' + text.slice(0,30) + '」 (' + (result.engine||'?') + ', ' + (result.time||'?') + 's)');
            document.getElementById('userSubtitle').textContent = '你: ' + text;
            var engineInfo = result.engine ? ' (' + result.engine + ')' : '';
            document.getElementById('statusLine').textContent = '识别耗时 ' + (result.time||'?') + 's' + engineInfo + ' | 发送中...';
            document.getElementById('statsLine').textContent = '';
            currentAiText = '';
            document.getElementById('aiSubtitle').textContent = '';
            sendText(text);
        }).catch(() => { document.getElementById('statusLine').textContent = '⚠️ 网络错误'; if (isInCall && !isMuted && !isPlaying) setTimeout(startRecording, 1000); });
    };
    mediaRecorder.start(100);
    isListening = true;
    silenceStart = null;
    startSilenceDetection();
}

function startSilenceDetection() {
    if (silenceTimer) clearInterval(silenceTimer);
    silenceTimer = setInterval(() => {
        if (!analyser || !mediaRecorder || mediaRecorder.state !== 'recording') { clearInterval(silenceTimer); return; }
        const data = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(data);
        const vol = data.reduce((a, b) => a + b, 0) / data.length;
        if (vol > SILENCE_THRESHOLD) { silenceStart = null; voiceFrameCount++; if (voiceFrameCount >= 3) hasVoiceActivity = true; }
        else { if (!silenceStart) silenceStart = Date.now(); else if (Date.now() - silenceStart > SILENCE_DURATION && Date.now() - recordStartTime > MIN_RECORD_TIME) { clearInterval(silenceTimer); stopRecording(); } }
    }, 100);
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
    isListening = false;
    if (silenceTimer) { clearInterval(silenceTimer); silenceTimer = null; }
}
function startListening() { if (isListening || isMuted || !isInCall || isAiSpeaking) return; if (mediaStream) startRecording(); }
function pauseListening() { stopRecording(); }
function resumeListening() { if (!isInCall || isMuted || isAiSpeaking) return; setTimeout(startListening, 800); }

// === 控制按钮 ===
// 音频解锁（解决浏览器自动播放限制）
var _audioUnlocked = false;
var _audioContext = null;
function unlockAudio() {
    if (_audioUnlocked) return;
    // 方式1: AudioContext 解锁（最可靠）
    try {
        _audioContext = new (window.AudioContext || window.webkitAudioContext)();
        var buffer = _audioContext.createBuffer(1, 1, 22050);
        var source = _audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(_audioContext.destination);
        source.start(0);
        if (_audioContext.state === 'suspended') {
            _audioContext.resume();
        }
    } catch(e) {}
    // 方式2: Audio 元素解锁
    try {
        var silence = new Audio('data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYYoRBqpAAAAAAD/+1DEAAAGAAGn9AAAIgAANP8AAAQAAAGkAAAAIAAANIAAAARMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVQ==');
        silence.play().then(function() {
            _audioUnlocked = true;
            addLog('INFO', '✅ 音频已解锁');
        }).catch(function() {});
    } catch(e) {}
    _audioUnlocked = true;
}

// 页面上任何点击/触摸都解锁音频
document.addEventListener('click', unlockAudio, {once: false});
document.addEventListener('touchstart', unlockAudio, {once: false});

function startCall() {
    unlockAudio();
    switchView('call');
    if (!isInCall) toggleCall();
    // 如果用户之前开启了视频，自动恢复
    if (_videoEnabled && !_videoStream) {
        setTimeout(function() { startVideo(); }, 500);
    }
}

function toggleCall() {
    if (!isInCall) {
        isInCall = true;
        audioSegments = [];
        _assistantMsgCount = 0;
        // 移除旧的下载按钮
        var oldBtn = document.getElementById('downloadCallBtn');
        if (oldBtn) oldBtn.remove();
        document.getElementById('btnCall').classList.add('active');
        document.getElementById('hint').textContent = '连接中...';
        document.getElementById('callStatus').style.display = 'none';
        document.getElementById('callTimer').style.display = 'block';
        document.getElementById('textInputArea').style.display = 'flex';
        callStartTime = Date.now();
        timerInterval = setInterval(updateTimer, 1000);
        updateTimer();
        if (!mediaStream) { initRecognition(); } else { startListening(); }
        updateStatus('connected', '已连接');
    } else {
        isInCall = false;
        _resetTitle();
        if (_bgNotif) { _bgNotif.close(); _bgNotif = null; }
        document.getElementById('btnCall').classList.remove('active');
        document.getElementById('hint').textContent = '通话已结束';
        document.getElementById('callStatus').style.display = 'block';
        document.getElementById('callTimer').style.display = 'none';
        document.getElementById('textInputArea').style.display = 'none';
        clearInterval(timerInterval);
        pauseListening();
        audioQueue = [];
        isPlaying = false;
        isAiSpeaking = false;
        // 挂断时停止视频
        if (_videoStream) stopVideo();
        updateStatus('disconnected', '未连接');
        // 生成通话小结并上传录音
        endCall();
    }
}

function toggleMute() {
    isMuted = !isMuted;
    document.getElementById('btnMute').classList.toggle('active', isMuted);
    if (isMuted) { pauseListening(); document.getElementById('hint').textContent = '已静音'; }
    else if (isInCall) startListening();
}

function toggleSpeaker() { document.getElementById('btnSpeaker').classList.toggle('active'); }

function sendTextInput() {
    if (isAiSpeaking || isPlaying) {
        document.getElementById('statusLine').textContent = '请等对方说完...';
        return;
    }
    const input = document.getElementById('textInput');
    const text = input.value.trim();
    if (!text || !isInCall) return;
    input.value = '';
    document.getElementById('userSubtitle').textContent = '你: ' + text;
    document.getElementById('statusLine').textContent = '发送中...';
    document.getElementById('statsLine').textContent = '';
    currentAiText = '';
    document.getElementById('aiSubtitle').textContent = '';
    sendText(text);
}

// === UI辅助 ===
function updateStatus(state, text) {
    const dot = document.getElementById('connDot');
    dot.className = 'conn-dot ' + state;
    const el = document.getElementById('callStatus');
    el.innerHTML = '';
    el.appendChild(dot);
    el.append(' ' + text);
}

function updateTimer() {
    if (!callStartTime) return;
    const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    const timeStr = m.toString().padStart(2,'0') + ':' + s.toString().padStart(2,'0');
    document.getElementById('callTimer').textContent = timeStr;
    // 标签页标题显示通话计时
    document.title = '通话中 ' + timeStr + ' · YLPhone';
}

// 通话结束时恢复标题
function _resetTitle() { document.title = '语音通话'; }

// 浏览器通知权限（进入页面时请求）
var _notifPermission = false;
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().then(function(p) { _notifPermission = (p === 'granted'); });
} else {
    _notifPermission = (Notification.permission === 'granted');
}

// 页面切到后台时弹通知
var _bgNotif = null;
document.addEventListener('visibilitychange', function() {
    if (document.hidden && isInCall && _notifPermission) {
        var elapsed = callStartTime ? Math.floor((Date.now() - callStartTime) / 1000) : 0;
        var m = Math.floor(elapsed / 60), s = elapsed % 60;
        var t = m.toString().padStart(2,'0') + ':' + s.toString().padStart(2,'0');
        _bgNotif = new Notification('YLPhone · 通话中', {
            body: '通话时长 ' + t + '\n点击返回通话',
            icon: '/static/icon-192.png',
            tag: 'voice-call',
            renotify: true,
            silent: true
        });
        _bgNotif.onclick = function() { window.focus(); _bgNotif.close(); };
    } else if (!document.hidden && _bgNotif) {
        _bgNotif.close();
        _bgNotif = null;
    }
});

// textInput回车发送
document.getElementById('textInput').addEventListener('keydown', e => { if (e.key === 'Enter') { sendTextInput(); e.preventDefault(); } });

// === 设置弹窗 ===
var _settingsModelLoaded = false;

function openSettings() {
    // 高亮当前主题按钮
    var currentTheme = getTheme();
    document.querySelectorAll('.theme-btn').forEach(function(btn) {
        var isActive = btn.getAttribute('data-theme-val') === currentTheme;
        btn.style.background = isActive ? '#0A84FF' : '';
        btn.style.fontWeight = isActive ? '600' : '400';
    });

    // 填充当前值
    document.getElementById('settingsUserInfo').value = _params.get('user_info') || '';
    document.getElementById('settingsTopic').value = _params.get('topic') || '';
    document.getElementById('settingsExtraPrompt').value = _params.get('extra_prompt') || '';
    document.getElementById('settingsMaxHistory').value = maxHistory;
    document.getElementById('settingsHistoryCount').value = historyCount || 0;

    // 内置模型
    if (!_settingsModelLoaded) {
        loadSettingsBuiltinModels();
        _settingsModelLoaded = true;
    }

    // LLM
    if (customLlm) {
        document.getElementById('settingsLlmUrl').value = customLlm.base_url || '';
        document.getElementById('settingsLlmKey').value = customLlm.api_key || '';
        document.getElementById('settingsLlmModel').value = customLlm.model || '';
    }

    // TTS
    if (customTts) {
        document.getElementById('settingsTtsKey').value = customTts.api_key || '';
        document.getElementById('settingsTtsGroupId').value = customTts.group_id || '';
        document.getElementById('settingsTtsVoiceId').value = customTts.voice_id || '';
        if (customTts.model) document.getElementById('settingsTtsModel').value = customTts.model;
    }

    // 初始化 LLM/TTS 切换状态
    _settingsUseBuiltinLlm = !customLlm;
    _settingsUseBuiltinTts = !customTts || !customTts.api_key;
    document.getElementById('togSettingsBuiltinLlm').classList.toggle('on', _settingsUseBuiltinLlm);
    document.getElementById('settingsBuiltinLlmPanel').style.display = _settingsUseBuiltinLlm ? 'block' : 'none';
    document.getElementById('settingsCustomLlmPanel').style.display = _settingsUseBuiltinLlm ? 'none' : 'block';
    document.getElementById('togSettingsBuiltinTts').classList.toggle('on', _settingsUseBuiltinTts);
    document.getElementById('settingsCustomTtsPanel').style.display = _settingsUseBuiltinTts ? 'none' : 'block';

    document.getElementById('settingsOverlay').classList.add('active');
    document.getElementById('settingsRetryCount').value = apiRetryCount;
}

function loadSettingsBuiltinModels() {
    var sel = document.getElementById('settingsBuiltinModel');
    sel.innerHTML = '<option value="" disabled selected>加载中...</option>';
    fetch('/api/builtin-models').then(r => r.json()).then(data => {
        var models = data.models || [];
        if (models.length === 0) {
            sel.innerHTML = '<option value="">无可用模型</option>';
            return;
        }
        sel.innerHTML = '';
        models.forEach(function(m) {
            var id = typeof m === 'string' ? m : (m.id || m.name || '');
            var opt = document.createElement('option');
            opt.value = id;
            opt.textContent = id;
            if (id === selectedModel) opt.selected = true;
            sel.appendChild(opt);
        });
    }).catch(function() {
        sel.innerHTML = '<option value="">获取失败</option>';
    });
}

function settingsFetchLlmModels() {
    var url = document.getElementById('settingsLlmUrl').value.trim();
    var key = document.getElementById('settingsLlmKey').value.trim();
    var errEl = document.getElementById('settingsLlmModelError');
    errEl.style.display = 'none';
    if (!url || !key) {
        errEl.textContent = '请先填写 API 地址和 Key';
        errEl.style.display = 'block';
        return;
    }
    fetch('/api/models?base_url=' + encodeURIComponent(url) + '&api_key=' + encodeURIComponent(key))
        .then(r => r.json())
        .then(data => {
            var models = data.models || [];
            if (models.length === 0) throw new Error('空列表');
            var input = document.getElementById('settingsLlmModel');
            var currentVal = input.value;
            var sel = document.createElement('select');
            sel.id = 'settingsLlmModel';
            sel.style.cssText = input.style.cssText;
            sel.className = input.className;
            var empty = document.createElement('option');
            empty.value = '';
            empty.textContent = '请选择...';
            sel.appendChild(empty);
            models.forEach(function(m) {
                var id = typeof m === 'string' ? m : (m.id || '');
                var opt = document.createElement('option');
                opt.value = id;
                opt.textContent = id;
                if (id === currentVal) opt.selected = true;
                sel.appendChild(opt);
            });
            input.parentNode.replaceChild(sel, input);
        })
        .catch(function(e) {
            errEl.textContent = '获取失败: ' + e.message;
            errEl.style.display = 'block';
        });
}

function closeSettings() {
    document.getElementById('settingsOverlay').classList.remove('active');
}

function saveSettings() {
    // 更新运行时变量
    var uInfo = document.getElementById('settingsUserInfo').value;
    var tp = document.getElementById('settingsTopic').value;
    var ep = document.getElementById('settingsExtraPrompt').value;

    // 重建 customPrompt
    customPrompt = '';
    if (userId) customPrompt += '# 用户称呼\n' + userId + '\n\n';
    if (uInfo) customPrompt += '# 关于用户\n' + uInfo + '\n\n';
    if (tp) customPrompt += '# 本次话题\n' + tp + '\n\n';
    if (ep) customPrompt += '# 补充设定\n' + ep + '\n\n';

    // 对话记忆
    var mh = parseInt(document.getElementById('settingsMaxHistory').value) || 20;
    maxHistory = Math.max(1, Math.min(100, mh));

    // 历史通话加载
    var hc = parseInt(document.getElementById('settingsHistoryCount').value) || 0;
    historyCount = Math.max(0, Math.min(10, hc));

    // 内置模型 / LLM
    if (_settingsUseBuiltinLlm) {
        var builtinSel = document.getElementById('settingsBuiltinModel');
        if (builtinSel && builtinSel.value) selectedModel = builtinSel.value;
        customLlm = null;
    } else {
        var llmUrl = document.getElementById('settingsLlmUrl').value.trim();
        var llmKey = document.getElementById('settingsLlmKey').value.trim();
        var llmModelEl = document.getElementById('settingsLlmModel');
        var llmModel = llmModelEl ? llmModelEl.value.trim() : '';
        if (llmUrl && llmKey) {
            customLlm = { base_url: llmUrl, api_key: llmKey, model: llmModel };
            selectedModel = '';
        }
    }

    if (_settingsUseBuiltinTts) {
        customTts = null;
    } else {
        var ttsKey = document.getElementById('settingsTtsKey').value.trim();
        if (ttsKey) {
            customTts = {
                api_key: ttsKey,
                group_id: document.getElementById('settingsTtsGroupId').value.trim(),
                voice_id: document.getElementById('settingsTtsVoiceId').value.trim(),
                model: document.getElementById('settingsTtsModel').value
            };
        } else {
            customTts = null;
        }
    }

    apiRetryCount = Math.max(0, Math.min(10, parseInt(document.getElementById('settingsRetryCount').value) || 3));
    try { localStorage.setItem('api_retry_count', apiRetryCount.toString()); } catch(e) {}
    closeSettings();
    // 显示保存确认
    document.getElementById('statusLine').textContent = '✅ 设置已保存并应用';
    setTimeout(function() {
        var sl = document.getElementById('statusLine');
        if (sl.textContent === '✅ 设置已保存并应用') sl.textContent = '';
    }, 2000);
}

// 设置弹窗的 LLM/TTS 切换
var _settingsUseBuiltinLlm = false;
var _settingsUseBuiltinTts = true;

function toggleSettingsLlmMode() {
    _settingsUseBuiltinLlm = !_settingsUseBuiltinLlm;
    document.getElementById('togSettingsBuiltinLlm').classList.toggle('on', _settingsUseBuiltinLlm);
    document.getElementById('settingsBuiltinLlmPanel').style.display = _settingsUseBuiltinLlm ? 'block' : 'none';
    document.getElementById('settingsCustomLlmPanel').style.display = _settingsUseBuiltinLlm ? 'none' : 'block';
    if (_settingsUseBuiltinLlm && !_settingsModelLoaded) {
        loadSettingsBuiltinModels();
        _settingsModelLoaded = true;
    }
}

function toggleSettingsTtsMode() {
    _settingsUseBuiltinTts = !_settingsUseBuiltinTts;
    document.getElementById('togSettingsBuiltinTts').classList.toggle('on', _settingsUseBuiltinTts);
    document.getElementById('settingsCustomTtsPanel').style.display = _settingsUseBuiltinTts ? 'none' : 'block';
}

// === 通话结束 & 录音上传 ===
function endCall() {
    var aiSub = document.getElementById('aiSubtitle');
    var statusLine = document.getElementById('statusLine');
    
    aiSub.textContent = '';
    statusLine.textContent = '正在保存通话记录...';
    
    // 复制后清空
    var chunksToSend = audioSegments.slice();
    audioSegments = [];
    
    var body = {
        token: TOKEN,
        session_id: sessionId,
        audio_chunks: chunksToSend,
        auto_memory: _autoMemoryEnabled
    };
    
    fetch('/api/end-call', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            statusLine.textContent = '⚠️ 保存失败: ' + data.error;
            return;
        }
        if (data.summary) {
            aiSub.textContent = '💌 ' + data.summary;
        }
        if (data.save_ok === false) {
            statusLine.textContent = '⚠️ 通话记录保存失败，请稍后重试';
        } else {
            statusLine.textContent = '✅ 通话记录已保存';
        }
        
        if (data.has_audio && data.audio_url) {
            showDownloadButton(data.audio_url);
        }
    }).catch(function(e) {
        statusLine.textContent = '⚠️ 保存失败，请检查网络';
        console.error('endCall error:', e);
    });
}

function showDownloadButton(audioUrl) {
    // 如果已有按钮就移除
    var existing = document.getElementById('downloadCallBtn');
    if (existing) existing.remove();
    
    var btn = document.createElement('a');
    btn.id = 'downloadCallBtn';
    btn.href = audioUrl + '?token=' + TOKEN;
    btn.download = 'call_recording.mp3';
    btn.textContent = '⬇️ 下载通话录音';
    btn.style.cssText = 'display:block;text-align:center;margin:12px auto;padding:12px 24px;background:#0A84FF;color:#fff;border-radius:12px;text-decoration:none;font-size:15px;font-weight:500;max-width:200px;';
    
    // 插入到 statsLine 后面
    var statsLine = document.getElementById('statsLine');
    if (statsLine && statsLine.parentNode) {
        statsLine.parentNode.insertBefore(btn, statsLine.nextSibling);
    }
}

// === 通话小结 ===
function fetchSummary() {
    var aiSub = document.getElementById('aiSubtitle');
    var statusLine = document.getElementById('statusLine');
    
    aiSub.textContent = '';
    statusLine.textContent = '正在生成通话小结...';
    
    var body = { session_id: sessionId, token: TOKEN };
    if (customLlm && customLlm.base_url && customLlm.api_key) body.custom_api = customLlm;
    if (selectedModel) body.model = selectedModel;
    
    fetch('/api/summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.summary) {
            aiSub.textContent = '💌 ' + data.summary;
            statusLine.textContent = '通话已结束';
        }
    }).catch(function(e) {
        statusLine.textContent = '通话已结束';
    });
}

// === 历史通话 ===
function loadCallHistory() {
    var list = document.getElementById('recentsList');
    list.innerHTML = '<div class="recents-empty" style="color:#8e8e93">正在加载通话记录...</div>';
    fetch('/api/call-history?token=' + TOKEN)
        .then(function(r) {
            if (r.status === 401) {
                list.innerHTML = '<div class="recents-empty">会话已过期，请重新登录</div>';
                return null;
            }
            return r.json();
        })
        .then(function(data) {
            if (!data) return;
            if (data.error) {
                list.innerHTML = '<div class="recents-empty">' + data.error + '</div>';
                return;
            }
            if (!data.calls || data.calls.length === 0) {
                // 判断是否口令用户
                var isAcct = false;
                try { isAcct = !!localStorage.getItem('account_token'); } catch(e) {}
                if (!isAcct) {
                    list.innerHTML = '<div class="recents-empty">口令登录不支持通话记录<br><span style="font-size:13px;color:#8e8e93">注册账号后可保存和查看历史通话</span></div>';
                } else {
                    list.innerHTML = '<div class="recents-empty">暂无通话记录</div>';
                }
                return;
            }
            list.innerHTML = data.calls.map(function(call) {
                return '<div class="recent-item" onclick="showCallDetail(\'' + call.call_id + '\', ' + (call.has_audio ? 'true' : 'false') + ')">' +
                    '<div class="recent-avatar">袁</div>' +
                    '<div class="recent-info">' +
                        '<div class="recent-name">' + (window.CHARACTER_NAME || 'AI助手') + '</div>' +
                        '<div class="recent-summary">' + (call.summary || call.rounds + '轮对话') + '</div>' +
                    '</div>' +
                    '<div class="recent-right">' +
                        '<div class="recent-time">' + formatCallTime(call.start_time) + '</div>' +
                        '<div class="recent-duration">' + formatCallDuration(call.duration) + '</div>' +
                    '</div>' +
                '</div>';
            }).join('');
        })
        .catch(function() {
            document.getElementById('recentsList').innerHTML = '<div class="recents-empty">加载失败，<a href="javascript:loadCallHistory()" style="color:#007aff;text-decoration:underline">点击重试</a></div>';
        });
}

function formatCallTime(isoStr) {
    if (!isoStr) return '';
    var d = new Date(isoStr);
    var now = new Date();
    var time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString()) return time;
    var yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return '昨天 ' + time;
    return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' + time;
}

function formatCallDuration(seconds) {
    if (!seconds || seconds <= 0) return '';
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    if (m > 0) return m + '分' + (s > 0 ? s + '秒' : '');
    return s + '秒';
}

// === 通话记录详情弹窗 ===
var _currentCallId = null;
var _currentCallHasAudio = false;
var _isCallLogEditing = false;

function showCallDetail(callId, hasAudio) {
    _currentCallId = callId;
    _currentCallHasAudio = hasAudio;

    fetch('/api/call-detail/' + callId + '?token=' + TOKEN)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) { alert(data.error); return; }

            document.getElementById('callLogTitle').textContent = formatCallTime(data.start_time) + ' 通话';

            var msgs = document.getElementById('callLogMessages');
            var segments = data.audio_segments || [];
            
            // 构建 assistant 消息序号 → 音频路径数组 的映射
            // 优先使用 msg_index（精确对应），回退到旧的顺序分组逻辑（兼容旧数据）
            var hasMsgIndex = segments.some(function(s) { return s.type === 'ai' && typeof s.msg_index === 'number'; });
            var aiAudioMap = {}; // assistantIndex → [path, ...]
            
            if (hasMsgIndex) {
                // 新逻辑：按 msg_index 精确分组
                for (var si = 0; si < segments.length; si++) {
                    if (segments[si].type === 'ai' && typeof segments[si].msg_index === 'number') {
                        var mi = segments[si].msg_index;
                        if (!aiAudioMap[mi]) aiAudioMap[mi] = [];
                        aiAudioMap[mi].push(segments[si].path);
                    }
                }
            } else {
                // 旧逻辑（兼容无 msg_index 的历史数据）：按 user 分组
                var aiGroupIndex = 0;
                var aiGroups = [];
                var currentGroup = [];
                for (var si = 0; si < segments.length; si++) {
                    if (segments[si].type === 'ai') {
                        currentGroup.push(segments[si].path);
                    } else {
                        if (currentGroup.length > 0) {
                            aiGroups.push(currentGroup);
                            currentGroup = [];
                        }
                    }
                }
                if (currentGroup.length > 0) aiGroups.push(currentGroup);
                for (var gi = 0; gi < aiGroups.length; gi++) {
                    aiAudioMap[gi] = aiGroups[gi];
                }
            }
            
            var assistantIdx = 0; // 当前是第几条 assistant 消息
            msgs.innerHTML = (data.messages || []).map(function(m, msgIdx) {
                var content = cleanVoiceTags(m.content);
                var timeStr = '';
                var timeMatch = content.match(/^\[(\d{4}\/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\]\s*/);
                if (timeMatch) {
                    timeStr = timeMatch[1];
                    content = content.replace(timeMatch[0], '');
                }
                
                var playBtn = '';
                if (m.role === 'assistant') {
                    var audioPaths = aiAudioMap[assistantIdx];
                    if (audioPaths && audioPaths.length > 0) {
                        var paths = audioPaths.join(',');
                        playBtn = '<button class="call-log-play-btn" onclick="playSegmentAudio(\'' + paths + '\')" title="播放">▶</button>';
                    }
                    assistantIdx++;
                }
                
                return '<div class="call-log-msg-wrap">' +
                    '<div class="call-log-msg ' + (m.role === 'user' ? 'user' : 'ai') + '">' + content + '</div>' +
                    '<div class="call-log-msg-footer">' + playBtn + (timeStr ? '<span class="call-log-msg-time">' + timeStr + '</span>' : '') + '</div>' +
                '</div>';
            }).join('');

            var rounds = (data.messages || []).filter(function(m) { return m.role === 'user'; }).length;
            document.getElementById('callLogMeta').textContent =
                rounds + '轮对话 · 通话时长 ' + formatCallDuration(data.duration);

            // 隐藏整体音频播放器（改为逐条播放）
            var audioDiv = document.getElementById('callLogAudio');
            audioDiv.style.display = 'none';

            // 显示记忆总结
            var memoryText = document.getElementById('callLogMemoryText');
            var memoryBtn = document.getElementById('btnGenerateMemory');
            if (data.memory) {
                memoryText.textContent = data.memory;
                memoryBtn.textContent = '重新生成';
            } else {
                memoryText.textContent = '暂无记忆';
                memoryBtn.textContent = '生成记忆';
            }

            document.getElementById('callLogModal').classList.add('active');
            setTimeout(function() { msgs.scrollTop = msgs.scrollHeight; }, 100);
        })
        .catch(function(e) { alert('加载失败: ' + e.message); });
}

function closeCallLogModal() {
    document.getElementById('callLogModal').classList.remove('active');
    _isCallLogEditing = false;
    document.getElementById('callLogActions').style.display = 'none';
    document.getElementById('callLogEditBtn').textContent = '编辑';
    // 停止音频
    var player = document.getElementById('callLogPlayer');
    if (player) { player.pause(); player.src = ''; }
    // 停止分段音频
    if (_segmentAudio) { _segmentAudio.pause(); _segmentAudio = null; }
    _segmentQueue = [];
}

function toggleCallLogEdit() {
    _isCallLogEditing = !_isCallLogEditing;
    var msgs = document.getElementById('callLogMessages');
    var actions = document.getElementById('callLogActions');
    var btn = document.getElementById('callLogEditBtn');

    if (_isCallLogEditing) {
        btn.textContent = '取消';
        actions.style.display = 'block';
        msgs.querySelectorAll('.call-log-msg').forEach(function(el) {
            el.contentEditable = 'true';
            el.style.outline = '1px dashed rgba(255,255,255,0.2)';
            el.style.cursor = 'text';
        });
    } else {
        btn.textContent = '编辑';
        actions.style.display = 'none';
        msgs.querySelectorAll('.call-log-msg').forEach(function(el) {
            el.contentEditable = 'false';
            el.style.outline = 'none';
            el.style.cursor = 'default';
        });
    }
}

function saveCallLogEdit() {
    if (!_currentCallId) return;
    var msgEls = document.getElementById('callLogMessages').querySelectorAll('.call-log-msg');
    var messages = [];
    msgEls.forEach(function(el) {
        messages.push({
            role: el.classList.contains('user') ? 'user' : 'assistant',
            content: el.textContent.trim()
        });
    });
    messages = messages.filter(function(m) { return m.content; });

    fetch('/api/call-detail/' + _currentCallId + '?token=' + TOKEN, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ messages: messages })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) { alert('保存失败: ' + result.error); return; }
        toggleCallLogEdit();
        loadCallHistory();
    })
    .catch(function(e) { alert('保存失败: ' + e.message); });
}

function deleteCallLog() {
    if (!_currentCallId || !confirm('确定删除这条通话记录？')) return;

    fetch('/api/call-detail/' + _currentCallId + '?token=' + TOKEN, { method: 'DELETE' })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) { alert('删除失败: ' + result.error); return; }
        closeCallLogModal();
        loadCallHistory();
    })
    .catch(function(e) { alert('删除失败: ' + e.message); });
}

function generateMemoryForCall() {
    if (!_currentCallId) return;
    var btn = document.getElementById('btnGenerateMemory');
    var textEl = document.getElementById('callLogMemoryText');
    btn.textContent = '生成中...';
    btn.disabled = true;
    
    // 传入当前 API 配置
    var body = {};
    if (customLlm && customLlm.base_url && customLlm.api_key) {
        body.custom_api = customLlm;
    }
    if (selectedModel) {
        body.model = selectedModel;
    }
    
    fetch('/api/generate-memory/' + _currentCallId + '?token=' + TOKEN, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) {
            alert('生成失败: ' + result.error);
            btn.textContent = '重试';
            btn.disabled = false;
            return;
        }
        textEl.textContent = result.memory;
        btn.textContent = '重新生成';
        btn.disabled = false;
        // 刷新记忆列表
        if (document.getElementById('viewProfile').classList.contains('active')) {
            loadMemorySummaries();
        }
    })
    .catch(function(e) {
        alert('生成失败: ' + e.message);
        btn.textContent = '重试';
        btn.disabled = false;
    });
}

var _segmentAudio = null;
var _segmentQueue = [];
function playSegmentAudio(pathsStr) {
    // 停止当前播放
    if (_segmentAudio) { _segmentAudio.pause(); _segmentAudio = null; }
    _segmentQueue = pathsStr.split(',').filter(function(p) { return p; });
    _playNextSegment();
}
function _playNextSegment() {
    if (_segmentQueue.length === 0) { _segmentAudio = null; return; }
    var path = _segmentQueue.shift();
    var url = '/api/audio-segment?path=' + encodeURIComponent(path) + '&token=' + TOKEN;
    _segmentAudio = new Audio(url);
    _segmentAudio.onended = function() { _playNextSegment(); };
    _segmentAudio.onerror = function() { _playNextSegment(); }; // 跳过失败的段
    _segmentAudio.play().catch(function() { _playNextSegment(); });
}

var _autoMemoryEnabled = false;
try { _autoMemoryEnabled = localStorage.getItem('auto_memory') === 'true'; } catch(e) {}

function toggleAutoMemory() {
    _autoMemoryEnabled = !_autoMemoryEnabled;
    var btn = document.getElementById('memoryAutoToggle');
    btn.classList.toggle('on', _autoMemoryEnabled);
    try { localStorage.setItem('auto_memory', _autoMemoryEnabled ? 'true' : 'false'); } catch(e) {}
}

function toggleSubtitle() {
    showSubtitle = !showSubtitle;
    try { localStorage.setItem('show_subtitle', showSubtitle ? 'true' : 'false'); } catch(e) {}
    // 更新开关状态
    var tog = document.getElementById('togSubtitle');
    if (tog) tog.classList.toggle('on', showSubtitle);
    var tog2 = document.getElementById('spTogSubtitle');
    if (tog2) tog2.classList.toggle('on', showSubtitle);
    var tog3 = document.getElementById('calluiTogSubtitle');
    if (tog3) tog3.classList.toggle('on', showSubtitle);
    // 立即显示/隐藏
    var area = document.querySelector('.subtitle-area');
    if (area) area.style.display = showSubtitle ? '' : 'none';
}

// 初始化开关状态
document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('memoryAutoToggle');
    if (btn && _autoMemoryEnabled) btn.classList.add('on');
    var togSub = document.getElementById('togSubtitle');
    if (togSub && showSubtitle) togSub.classList.add('on');
    var spTogSub = document.getElementById('spTogSubtitle');
    if (spTogSub && showSubtitle) spTogSub.classList.add('on');
    // 账号用户：从后端加载保存的设定
    if (TOKEN) {
        fetch('/api/account/load-settings?token=' + TOKEN)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.user_id && !userId) userId = data.user_id;
                if (data.user_info && !userInfo) userInfo = data.user_info;
                if (data.topic && !topic) topic = data.topic;
                if (data.extra_prompt && !extraPrompt) extraPrompt = data.extra_prompt;
                // 重建 customPrompt
                if (userId || userInfo || topic || extraPrompt) {
                    customPrompt = '';
                    if (userId) customPrompt += '# 用户称呼\n' + userId + '\n\n';
                    if (userInfo) customPrompt += '# 关于用户\n' + userInfo + '\n\n';
                    if (topic) customPrompt += '# 本次话题\n' + topic + '\n\n';
                    if (extraPrompt) customPrompt += '# 补充设定\n' + extraPrompt + '\n\n';
                }
                // 同步到 localStorage
                try {
                    if (userId) localStorage.setItem('vc_userId', userId);
                    if (userInfo) localStorage.setItem('vc_userInfo', userInfo);
                    if (topic) localStorage.setItem('vc_topic', topic);
                    if (extraPrompt) localStorage.setItem('vc_extraPrompt', extraPrompt);
                } catch(e) {}
            })
            .catch(function() {});
    }
});

// === 子页面管理 ===
function openSubPage(name) {
    var id = 'sub' + name.charAt(0).toUpperCase() + name.slice(1);
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add('active');
    
    // 初始化子页面数据
    if (name === 'personal') {
        document.getElementById('spUserId').value = userId || '';
        document.getElementById('spUserInfo').value = userInfo || '';
        document.getElementById('spTopic').value = topic || '';
        document.getElementById('spExtraPrompt').value = extraPrompt || '';
    } else if (name === 'memory') {
        loadMemorySummaries();
    } else if (name === 'apiconfig') {
        // 判断当前模式
        _useBuiltinLlm = !customLlm;
        _useBuiltinTts = !customTts || !customTts.api_key;
        
        document.getElementById('togBuiltinLlm').classList.toggle('on', _useBuiltinLlm);
        document.getElementById('builtinLlmPanel').style.display = _useBuiltinLlm ? 'block' : 'none';
        document.getElementById('customLlmPanel').style.display = _useBuiltinLlm ? 'none' : 'block';
        
        document.getElementById('togBuiltinTts').classList.toggle('on', _useBuiltinTts);
        document.getElementById('customTtsPanel').style.display = _useBuiltinTts ? 'none' : 'block';
        
        if (customLlm) {
            document.getElementById('spLlmUrl').value = customLlm.base_url || '';
            document.getElementById('spLlmKey').value = customLlm.api_key || '';
            document.getElementById('spLlmModel').value = customLlm.model || '';
        }
        if (customTts) {
            document.getElementById('spTtsKey').value = customTts.api_key || '';
            document.getElementById('spTtsGroupId').value = customTts.group_id || '';
            document.getElementById('spTtsVoiceId').value = customTts.voice_id || '';
            if (customTts.model) document.getElementById('spTtsModel').value = customTts.model;
        }
        if (_useBuiltinLlm) spLoadBuiltinModels();
        // 识图模型
        var _useCustomVision = !!(customVision && customVision.base_url && customVision.api_key);
        document.getElementById('togVisionMode').classList.toggle('on', _useCustomVision);
        document.getElementById('customVisionPanel').style.display = _useCustomVision ? 'block' : 'none';
        if (customVision) {
            document.getElementById('spVisionUrl').value = customVision.base_url || '';
            document.getElementById('spVisionKey').value = customVision.api_key || '';
            document.getElementById('spVisionModel').value = customVision.model || '';
        } else {
            document.getElementById('spVisionUrl').value = '';
            document.getElementById('spVisionKey').value = '';
            document.getElementById('spVisionModel').value = '';
        }
    } else if (name === 'context') {
        document.getElementById('spMaxHistory').value = maxHistory || 20;
        document.getElementById('spHistoryCount').value = historyCount || 0;
    } else if (name === 'callui') {
        var togSub = document.getElementById('calluiTogSubtitle');
        if (togSub) togSub.classList.toggle('on', showSubtitle);
        document.getElementById('calluiRetryCount').value = apiRetryCount;
        // 视频开关
        var togVideo = document.getElementById('calluiTogVideo');
        if (togVideo) togVideo.classList.toggle('on', _videoEnabled);
    }
}

function closeSubPage(id) {
    document.getElementById(id).classList.remove('active');
}

function savePersonalSettings() {
    userId = document.getElementById('spUserId').value.trim();
    userInfo = document.getElementById('spUserInfo').value;
    topic = document.getElementById('spTopic').value;
    extraPrompt = document.getElementById('spExtraPrompt').value;
    // 重建 customPrompt
    customPrompt = '';
    if (userId) customPrompt += '# 用户称呼\n' + userId + '\n\n';
    if (userInfo) customPrompt += '# 关于用户\n' + userInfo + '\n\n';
    if (topic) customPrompt += '# 本次话题\n' + topic + '\n\n';
    if (extraPrompt) customPrompt += '# 补充设定\n' + extraPrompt + '\n\n';
    // 持久化到 localStorage
    try {
        localStorage.setItem('vc_userId', userId);
        localStorage.setItem('vc_userInfo', userInfo);
        localStorage.setItem('vc_topic', topic);
        localStorage.setItem('vc_extraPrompt', extraPrompt);
    } catch(e) {}
    // 同步保存到后端（账号用户）
    if (TOKEN) {
        fetch('/api/account/save-settings?token=' + TOKEN, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({user_id: userId, user_info: userInfo, topic: topic, extra_prompt: extraPrompt})
        }).catch(function(){});
    }
    closeSubPage('subPersonal');
}

function saveApiSettings() {
    if (_useBuiltinLlm) {
        // 使用内置模型
        var builtinSel = document.getElementById('spBuiltinModel');
        if (builtinSel && builtinSel.value) selectedModel = builtinSel.value;
        customLlm = null; // 清掉自定义
    } else {
        // 使用自定义 API
        var llmUrl = document.getElementById('spLlmUrl').value.trim();
        var llmKey = document.getElementById('spLlmKey').value.trim();
        var llmModel = document.getElementById('spLlmModel').value.trim();
        if (llmUrl && llmKey) {
            customLlm = { base_url: llmUrl, api_key: llmKey, model: llmModel };
            selectedModel = ''; // 清掉内置选择
        }
    }
    
    if (_useBuiltinTts) {
        customTts = null; // 使用内置TTS
    } else {
        var ttsKey = document.getElementById('spTtsKey').value.trim();
        if (ttsKey) {
            customTts = {
                api_key: ttsKey,
                group_id: document.getElementById('spTtsGroupId').value.trim(),
                voice_id: document.getElementById('spTtsVoiceId').value.trim(),
                model: document.getElementById('spTtsModel').value
            };
        }
    }
    // 识图模型
    var visionUrl = document.getElementById('spVisionUrl').value.trim();
    var visionKey = document.getElementById('spVisionKey').value.trim();
    var visionModel = document.getElementById('spVisionModel').value.trim();
    if (visionUrl && visionKey) {
        customVision = { base_url: visionUrl, api_key: visionKey, model: visionModel || 'gpt-4o-mini' };
        try { localStorage.setItem('vc_customVision', JSON.stringify(customVision)); } catch(e) {}
    } else {
        customVision = null;
        try { localStorage.removeItem('vc_customVision'); } catch(e) {}
    }
    closeSubPage('subApiconfig');
}

function clearVisionConfig() {
    customVision = null;
    try { localStorage.removeItem('vc_customVision'); } catch(e) {}
    document.getElementById('spVisionUrl').value = '';
    document.getElementById('spVisionKey').value = '';
    document.getElementById('spVisionModel').value = '';
}

var _useCustomVision = false;
function toggleVisionMode() {
    _useCustomVision = !_useCustomVision;
    document.getElementById('togVisionMode').classList.toggle('on', _useCustomVision);
    document.getElementById('customVisionPanel').style.display = _useCustomVision ? 'block' : 'none';
    if (!_useCustomVision) {
        clearVisionConfig();
    }
}

function saveContextSettings() {
    maxHistory = Math.max(1, Math.min(100, parseInt(document.getElementById('spMaxHistory').value) || 20));
    historyCount = Math.max(0, Math.min(10, parseInt(document.getElementById('spHistoryCount').value) || 0));
    closeSubPage('subContext');
}

function saveCalluiSettings() {
    apiRetryCount = Math.max(0, Math.min(10, parseInt(document.getElementById('calluiRetryCount').value) || 3));
    try { localStorage.setItem('api_retry_count', apiRetryCount.toString()); } catch(e) {}
    closeSubPage('subCallui');
}

function spLoadBuiltinModels() {
    var sel = document.getElementById('spBuiltinModel');
    sel.innerHTML = '<option value="" disabled selected>加载中...</option>';
    fetch('/api/builtin-models').then(function(r) { return r.json(); }).then(function(data) {
        var models = data.models || [];
        if (!models.length) { sel.innerHTML = '<option value="">无可用</option>'; return; }
        sel.innerHTML = '';
        models.forEach(function(m) {
            var id = typeof m === 'string' ? m : (m.id || '');
            var opt = document.createElement('option');
            opt.value = id; opt.textContent = id;
            if (id === selectedModel) opt.selected = true;
            sel.appendChild(opt);
        });
    }).catch(function() { sel.innerHTML = '<option value="">获取失败</option>'; });
}

function spFetchModels() {
    var url = document.getElementById('spLlmUrl').value.trim();
    var key = document.getElementById('spLlmKey').value.trim();
    if (!url || !key) { alert('请先填写 API 地址和 Key'); return; }
    fetch('/api/models?base_url=' + encodeURIComponent(url) + '&api_key=' + encodeURIComponent(key))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var models = data.models || [];
            if (!models.length) { alert('获取失败：空列表'); return; }
            var input = document.getElementById('spLlmModel');
            var sel = document.createElement('select');
            sel.id = 'spLlmModel';
            sel.style.cssText = input.style.cssText;
            models.forEach(function(m) {
                var id = typeof m === 'string' ? m : (m.id || '');
                var opt = document.createElement('option');
                opt.value = id; opt.textContent = id;
                if (id === input.value) opt.selected = true;
                sel.appendChild(opt);
            });
            input.parentNode.replaceChild(sel, input);
        })
        .catch(function(e) { alert('获取失败: ' + e.message); });
}

function spFetchVisionModels() {
    var url = document.getElementById('spVisionUrl').value.trim();
    var key = document.getElementById('spVisionKey').value.trim();
    if (!url || !key) { alert('请先填写 API 地址和 Key'); return; }
    fetch('/api/models?base_url=' + encodeURIComponent(url) + '&api_key=' + encodeURIComponent(key))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var models = data.models || [];
            if (!models.length) { alert('获取失败：空列表'); return; }
            var input = document.getElementById('spVisionModel');
            var sel = document.createElement('select');
            sel.id = 'spVisionModel';
            sel.style.cssText = input.style.cssText;
            models.forEach(function(m) {
                var id = typeof m === 'string' ? m : (m.id || '');
                var opt = document.createElement('option');
                opt.value = id; opt.textContent = id;
                if (id === input.value) opt.selected = true;
                sel.appendChild(opt);
            });
            input.parentNode.replaceChild(sel, input);
        })
        .catch(function(e) { alert('获取失败: ' + e.message); });
}

var _useBuiltinLlm = false;
var _useBuiltinTts = true;

function toggleLlmMode() {
    _useBuiltinLlm = !_useBuiltinLlm;
    document.getElementById('togBuiltinLlm').classList.toggle('on', _useBuiltinLlm);
    document.getElementById('builtinLlmPanel').style.display = _useBuiltinLlm ? 'block' : 'none';
    document.getElementById('customLlmPanel').style.display = _useBuiltinLlm ? 'none' : 'block';
    if (_useBuiltinLlm) spLoadBuiltinModels();
}

function toggleTtsMode() {
    _useBuiltinTts = !_useBuiltinTts;
    document.getElementById('togBuiltinTts').classList.toggle('on', _useBuiltinTts);
    document.getElementById('customTtsPanel').style.display = _useBuiltinTts ? 'none' : 'block';
}

// 通话页面子页面的视频开关
function toggleVideoSetting() {
    if (_videoEnabled) {
        stopVideo();
    } else {
        startVideo();
    }
    // 同步开关 UI
    var tog = document.getElementById('calluiTogVideo');
    if (tog) tog.classList.toggle('on', _videoEnabled);
    var btn = document.getElementById('btnVideo');
    if (btn) btn.classList.toggle('active', _videoEnabled);
}

// === 通话总结记忆管理 ===
function loadMemorySummaries() {
    var container = document.getElementById('memorySummaries');
    container.innerHTML = '<div class="profile-loading">加载中...</div>';
    
    fetch('/api/call-history?token=' + TOKEN)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var calls = (data.calls || []).filter(function(c) { return c.memory || c.summary; });
            if (calls.length === 0) {
                container.innerHTML = '<div class="memory-empty">暂无通话记忆<br><span style="font-size:12px;color:#636366">通话结束后会自动生成记忆总结</span></div>';
                return;
            }
            container.innerHTML = calls.map(function(call, idx) {
                return '<div class="memory-item" id="memory-' + call.call_id + '">' +
                    '<div class="memory-item-header">' +
                        '<div class="memory-item-time">' + formatCallTime(call.start_time) + ' · ' + call.rounds + '轮对话</div>' +
                        '<div class="memory-item-actions">' +
                            '<button onclick="editMemory(\'' + call.call_id + '\')">编辑</button>' +
                            '<button onclick="deleteMemory(\'' + call.call_id + '\')" style="color:#FF3B30">删除</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="memory-item-text" id="memory-text-' + call.call_id + '">' + (call.memory || call.summary || '') + '</div>' +
                '</div>';
            }).join('');
        })
        .catch(function() {
            container.innerHTML = '<div class="memory-empty">加载失败，请检查网络</div>';
        });
}

function editMemory(callId) {
    var textEl = document.getElementById('memory-text-' + callId);
    if (!textEl) return;
    
    if (textEl.contentEditable === 'true') {
        // 保存
        var newText = textEl.textContent.trim();
        textEl.contentEditable = 'false';
        
        fetch('/api/call-detail/' + callId + '?token=' + TOKEN, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ memory: newText })
        })
        .then(function(r) { return r.json(); })
        .then(function(result) {
            if (result.error) {
                alert('保存失败: ' + result.error);
                return;
            }
            // 更新按钮文字
            var btn = textEl.closest('.memory-item').querySelector('.memory-item-actions button');
            if (btn) btn.textContent = '编辑';
        })
        .catch(function(e) { alert('保存失败: ' + e.message); });
        
        // 更新按钮文字
        var btn = textEl.closest('.memory-item').querySelector('.memory-item-actions button');
        if (btn) btn.textContent = '编辑';
    } else {
        // 开始编辑
        textEl.contentEditable = 'true';
        textEl.focus();
        // 更新按钮文字
        var btn = textEl.closest('.memory-item').querySelector('.memory-item-actions button');
        if (btn) btn.textContent = '保存';
    }
}

function deleteMemory(callId) {
    if (!confirm('删除后该通话的记忆总结将不再提供给AI，确定？')) return;
    
    // 只清空 summary，不删除整条记录
    fetch('/api/call-detail/' + callId + '?token=' + TOKEN, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ memory: '' })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) { alert('删除失败: ' + result.error); return; }
        // 从列表中移除
        var el = document.getElementById('memory-' + callId);
        if (el) el.remove();
        // 检查是否为空
        var container = document.getElementById('memorySummaries');
        if (!container.querySelector('.memory-item')) {
            container.innerHTML = '<div class="memory-empty">暂无通话记忆</div>';
        }
    })
    .catch(function(e) { alert('删除失败: ' + e.message); });
}

// 页面加载时加载历史通话（如果有 token 的话）
if (TOKEN) {
    loadCallHistory();
}
