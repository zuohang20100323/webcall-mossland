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

  // 原封不动保留所有的 JavaScript 逻辑
  
  // 获取 token
  var token = new URLSearchParams(window.location.search).get('token');
  if (!token) {
    window.location.href = '/';
  }

  // 页面加载 - 加载内置模型列表
  loadBuiltinModels();

  // LLM 模式切换
  function onLlmModeChange() {
    var mode = document.getElementById('llmMode').value;
    document.getElementById('llmBuiltinSection').style.display = mode === 'builtin' ? 'block' : 'none';
    document.getElementById('llmCustomSection').style.display = mode === 'custom' ? 'block' : 'none';
  }

  // TTS 模式切换
  function onTtsModeChange() {
    var mode = document.getElementById('ttsMode').value;
    document.getElementById('ttsApiKeySection').style.display = mode === 'apikey' ? 'block' : 'none';
    document.getElementById('ttsCustomSection').style.display = mode === 'custom' ? 'block' : 'none';
  }

  // 获取内置模型列表
  function loadBuiltinModels() {
    var container = document.getElementById('builtinModelContainer');
    var select = document.getElementById('builtinModelSelect');

    fetch('/api/builtin-models')
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        var models = data.models || data.data || data;
        if (!Array.isArray(models) || models.length === 0) {
          throw new Error('空列表');
        }

        // 清空 select 并填充
        select.innerHTML = '';
        models.forEach(function(m) {
          var modelId = typeof m === 'string' ? m : (m.id || m.name || '');
          var opt = document.createElement('option');
          opt.value = modelId;
          opt.textContent = modelId;
          select.appendChild(opt);
        });
      })
      .catch(function(err) {
        // 失败时替换为手动输入框
        container.innerHTML = '<div class="error-msg" style="display:block;margin-bottom:8px">获取模型列表失败</div>' +
          '<input type="text" id="builtinModelInput" placeholder="手动输入模型名称">';
      });
  }

  // 折叠/展开
  function toggleSection(id) {
    var content = document.getElementById('section-' + id);
    var arrow = document.getElementById('arrow-' + id);
    var isOpen = content.classList.contains('open');
    if (isOpen) {
      content.classList.remove('open');
      arrow.textContent = '▶';
    } else {
      content.classList.add('open');
      arrow.textContent = '▼';
    }
  }

  // 导入文件
  function importFile(event) {
    var file = event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(e) {
      document.getElementById('extraPrompt').value = e.target.result;
    };
    reader.readAsText(file);
    event.target.value = '';
  }

  // 获取模型列表
  function fetchModels() {
    var baseUrl = document.getElementById('llmBaseUrl').value.trim();
    var apiKey = document.getElementById('llmApiKey').value.trim();
    var errorEl = document.getElementById('modelError');
    errorEl.style.display = 'none';

    if (!baseUrl) {
      errorEl.textContent = '请先填写 API 地址';
      errorEl.style.display = 'block';
      return;
    }

    var url = '/api/models?base_url=' + encodeURIComponent(baseUrl);
    if (apiKey) {
      url += '&api_key=' + encodeURIComponent(apiKey);
    }

    fetch(url)
      .then(function(res) {
        if (!res.ok) throw new Error('请求失败 (' + res.status + ')');
        return res.json();
      })
      .then(function(data) {
        var models = data.models || data.data || data;
        if (!Array.isArray(models) || models.length === 0) {
          throw new Error('未获取到模型列表');
        }

        // 获取当前值
        var currentModel = '';
        var modelEl = document.getElementById('llmModel');
        if (modelEl) currentModel = modelEl.value;

        // 创建 select 替换 input
        var select = document.createElement('select');
        select.id = 'llmModel';

        // 空选项
        var emptyOpt = document.createElement('option');
        emptyOpt.value = '';
        emptyOpt.textContent = '请选择模型...';
        select.appendChild(emptyOpt);

        models.forEach(function(m) {
          var opt = document.createElement('option');
          var modelId = typeof m === 'string' ? m : (m.id || m.name || '');
          opt.value = modelId;
          opt.textContent = modelId;
          if (modelId === currentModel) opt.selected = true;
          select.appendChild(opt);
        });

        // 替换
        var container = modelEl.parentNode;
        container.replaceChild(select, modelEl);
      })
      .catch(function(err) {
        errorEl.textContent = '获取失败：' + err.message;
        errorEl.style.display = 'block';
      });
  }

  // === 设定加密云端持久化 ===
  // 用户密码派生 AES-GCM 密钥，服务端只存密文
  var _SETUP_STORAGE_KEY = 'voice_setup_v1';
  var _setupCryptoKey = null;  // CryptoKey 对象
  var _accountToken = null;    // 账号 token

  // 从密码派生 AES 密钥
  async function _deriveKey(password) {
    var enc = new TextEncoder();
    var keyMaterial = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      {name: 'PBKDF2', salt: enc.encode('voice-call-setup-salt'), iterations: 100000, hash: 'SHA-256'},
      keyMaterial, {name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']
    );
  }

  // 加密
  async function _encryptSettings(data, key) {
    var enc = new TextEncoder();
    var iv = crypto.getRandomValues(new Uint8Array(12));
    var ct = await crypto.subtle.encrypt({name: 'AES-GCM', iv: iv}, key, enc.encode(JSON.stringify(data)));
    // iv + ciphertext → base64
    var buf = new Uint8Array(iv.length + ct.byteLength);
    buf.set(iv); buf.set(new Uint8Array(ct), iv.length);
    return btoa(String.fromCharCode.apply(null, buf));
  }

  // 解密
  async function _decryptSettings(b64, key) {
    try {
      var bin = atob(b64);
      var buf = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
      var iv = buf.slice(0, 12);
      var ct = buf.slice(12);
      var dec = await crypto.subtle.decrypt({name: 'AES-GCM', iv: iv}, key, ct);
      return JSON.parse(new TextDecoder().decode(dec));
    } catch(e) { return null; }
  }

  // 初始化加密系统（登录后调用）
  async function initSettingsCrypto(accountToken, password) {
    _accountToken = accountToken;
    try {
      _setupCryptoKey = await _deriveKey(password);
    } catch(e) { _setupCryptoKey = null; }
  }

  // 从云端加载设定
  async function _loadCloudSettings() {
    if (!_accountToken || !_setupCryptoKey) return;
    try {
      var resp = await fetch('/api/account/settings?token=' + _accountToken);
      if (!resp.ok) return;
      var data = await resp.json();
      if (data.encrypted) {
        var settings = await _decryptSettings(data.encrypted, _setupCryptoKey);
        if (settings) { _applySettings(settings); return; }
      }
    } catch(e) {}
  }

  // 保存设定到云端
  async function _saveCloudSettings() {
    if (!_accountToken || !_setupCryptoKey) return;
    try {
      var d = _collectSettings();
      var encrypted = await _encryptSettings(d, _setupCryptoKey);
      fetch('/api/account/settings?token=' + _accountToken, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({encrypted: encrypted})
      });
    } catch(e) {}
  }

  function _applySettings(d) {
    if (!d) return;
    if (d.userInfo) document.getElementById('userInfo').value = d.userInfo;
    if (d.topic) document.getElementById('topic').value = d.topic;
    if (d.extraPrompt) document.getElementById('extraPrompt').value = d.extraPrompt;
    if (d.filterRegex) { var el = document.getElementById('filterRegex'); if (el) el.value = d.filterRegex; }
    if (d.filterWords) { var el = document.getElementById('filterWords'); if (el) el.value = d.filterWords; }
    if (d.llmMode) {
      var llmModeEl = document.getElementById('llmMode');
      if (llmModeEl) { llmModeEl.value = d.llmMode; onLlmModeChange(); }
    }
    if (d.ttsMode) {
      var ttsModeEl = document.getElementById('ttsMode');
      if (ttsModeEl) { ttsModeEl.value = d.ttsMode; onTtsModeChange(); }
    }
    if (d.llmBaseUrl) { var el = document.getElementById('llmBaseUrl'); if (el) el.value = d.llmBaseUrl; }
    if (d.llmApiKey) { var el = document.getElementById('llmApiKey'); if (el) el.value = d.llmApiKey; }
    if (d.llmModel) { var el = document.getElementById('llmModel'); if (el) el.value = d.llmModel; }
    if (d.ttsApiKeyInput) { var el = document.getElementById('ttsApiKeyInput'); if (el) el.value = d.ttsApiKeyInput; }
    if (d.ttsApiKey) { var el = document.getElementById('ttsApiKey'); if (el) el.value = d.ttsApiKey; }
    if (d.ttsGroupId) { var el = document.getElementById('ttsGroupId'); if (el) el.value = d.ttsGroupId; }
    if (d.ttsVoiceId) { var el = document.getElementById('ttsVoiceId'); if (el) el.value = d.ttsVoiceId; }
    if (d.ttsModel) { var el = document.getElementById('ttsModel'); if (el) el.value = d.ttsModel; }
    if (d.builtinModel) {
      setTimeout(function() {
        var sel = document.getElementById('builtinModelSelect');
        if (sel) sel.value = d.builtinModel;
      }, 1000);
    }
  }

  function _collectSettings() {
    var d = {};
    d.userInfo = (document.getElementById('userInfo').value || '').trim();
    d.topic = (document.getElementById('topic').value || '').trim();
    d.extraPrompt = (document.getElementById('extraPrompt').value || '').trim();
    var frEl = document.getElementById('filterRegex'); if (frEl) d.filterRegex = frEl.value.trim();
    var fwEl = document.getElementById('filterWords'); if (fwEl) d.filterWords = fwEl.value.trim();
    var llmModeEl = document.getElementById('llmMode');
    if (llmModeEl) d.llmMode = llmModeEl.value;
    var ttsModeEl = document.getElementById('ttsMode');
    if (ttsModeEl) d.ttsMode = ttsModeEl.value;
    var el;
    el = document.getElementById('llmBaseUrl'); if (el) d.llmBaseUrl = el.value.trim();
    el = document.getElementById('llmApiKey'); if (el) d.llmApiKey = el.value.trim();
    el = document.getElementById('llmModel'); if (el) d.llmModel = el.value.trim();
    el = document.getElementById('ttsApiKeyInput'); if (el) d.ttsApiKeyInput = el.value.trim();
    el = document.getElementById('ttsApiKey'); if (el) d.ttsApiKey = el.value.trim();
    el = document.getElementById('ttsGroupId'); if (el) d.ttsGroupId = el.value.trim();
    el = document.getElementById('ttsVoiceId'); if (el) d.ttsVoiceId = el.value.trim();
    el = document.getElementById('ttsModel'); if (el) d.ttsModel = el.value;
    el = document.getElementById('builtinModelSelect'); if (el) d.builtinModel = el.value;
    return d;
  }

  function _loadSavedSetup() {
    // 优先云端（有账号时），降级 localStorage
    if (_accountToken && _setupCryptoKey) {
      _loadCloudSettings();
      return;
    }
    try {
      var saved = localStorage.getItem(_SETUP_STORAGE_KEY);
      if (!saved) return;
      _applySettings(JSON.parse(saved));
    } catch(e) {}
  }

  function _saveCurrentSetup() {
    var d = _collectSettings();
    // 存 localStorage（降级/免登录用户）
    try { localStorage.setItem(_SETUP_STORAGE_KEY, JSON.stringify(d)); } catch(e) {}
    // 有账号则同步云端
    if (_accountToken && _setupCryptoKey) { _saveCloudSettings(); }
  }

  // 页面加载后：初始化加密 + 恢复设定
  (async function() {
    try {
      var savedToken = localStorage.getItem('account_token');
      var pwSeed = localStorage.getItem('account_pw_seed');
      if (savedToken && pwSeed) {
        await initSettingsCrypto(savedToken, pwSeed);
      }
    } catch(e) {}
    setTimeout(_loadSavedSetup, 300);
  })();

  // 开始通话
  function startCall() {
    // 保存设定到 localStorage
    _saveCurrentSetup();

    var ttsMode = document.getElementById('ttsMode').value;

    // 收集所有数据
    var userInfo = document.getElementById('userInfo').value || '';
    var topicVal = document.getElementById('topic').value || '';
    var extraPromptVal = document.getElementById('extraPrompt').value || '';

    // 模型选择（根据 LLM 模式）
    var model = '';
    var customLlm = '';
    var llmMode = document.getElementById('llmMode') ? document.getElementById('llmMode').value : 'builtin';
    
    if (llmMode === 'builtin') {
      var builtinSelect = document.getElementById('builtinModelSelect');
      var builtinInput = document.getElementById('builtinModelInput');
      if (builtinSelect) model = builtinSelect.value || '';
      else if (builtinInput) model = builtinInput.value.trim() || '';
    } else {
      var llmUrl = document.getElementById('llmBaseUrl').value.trim();
      var llmKey = document.getElementById('llmApiKey').value.trim();
      var llmModelVal = document.getElementById('llmModel').value.trim();
      if (llmUrl && llmKey) {
        customLlm = JSON.stringify({base_url: llmUrl, api_key: llmKey, model: llmModelVal});
      }
    }

    // TTS 配置
    var customTts = '';
    var ttsApiKeyParam = '';

    if (ttsMode === 'apikey') {
      // 使用 TTS API Key（走计费）
      ttsApiKeyParam = document.getElementById('ttsApiKeyInput').value.trim();
    } else if (ttsMode === 'custom') {
      // 使用自己的 MiniMax
      var ttsKeyVal = document.getElementById('ttsApiKey').value.trim();
      var ttsGroupVal = document.getElementById('ttsGroupId').value.trim();
      var ttsVoiceVal = document.getElementById('ttsVoiceId').value.trim();
      var ttsModelVal = document.getElementById('ttsModel').value;
      if (ttsKeyVal) {
        var ttsObj = {api_key: ttsKeyVal};
        if (ttsGroupVal) ttsObj.group_id = ttsGroupVal;
        if (ttsVoiceVal) ttsObj.voice_id = ttsVoiceVal;
        if (ttsModelVal) ttsObj.model = ttsModelVal;
        customTts = JSON.stringify(ttsObj);
      }
    }
    // builtin: 不传任何 TTS 参数，后端用内置配置

    // 编码到 sessionStorage（不暴露在 URL 中）
    var callData = {};
    if (userInfo) callData.user_info = userInfo;
    if (topicVal) callData.topic = topicVal;
    if (extraPromptVal) callData.extra_prompt = extraPromptVal;
    if (model) callData.model = model;
    if (customLlm) callData.custom_llm = customLlm;
    if (customTts) callData.custom_tts = customTts;
    if (ttsApiKeyParam) callData.tts_api_key = ttsApiKeyParam;

    // 构建 filter_rules
    var filterRules = [];
    var filterRegexEl = document.getElementById('filterRegex');
    var filterWordsEl = document.getElementById('filterWords');
    if (filterRegexEl && filterRegexEl.value.trim()) {
      var lines = filterRegexEl.value.trim().split('\n');
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (line) {
          filterRules.push({pattern: line, replace: ''});
        }
      }
    }
    if (filterWordsEl && filterWordsEl.value.trim()) {
      var words = filterWordsEl.value.trim().split('\n');
      for (var i = 0; i < words.length; i++) {
        var word = words[i].trim();
        if (word) {
          // 精确匹配：转义正则特殊字符
          var escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          filterRules.push({pattern: escaped, replace: ''});
        }
      }
    }
    if (filterRules.length > 0) callData.filter_rules = JSON.stringify(filterRules);

    try {
      sessionStorage.setItem('call_data', JSON.stringify(callData));
    } catch(e) {}

    // URL 只传 token（不泄露用户内容）
    window.location.href = '/call?token=' + token;
  }
