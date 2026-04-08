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

// === 口令体验 ===
var redeemInput = document.getElementById('redeemInput');
var redeemBtn = document.getElementById('redeemBtn');
var redeemError = document.getElementById('redeemError');

redeemInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doRedeem();
});

async function doRedeem() {
    var code = redeemInput.value.trim();
    redeemError.textContent = '';
    if (!code) {
        redeemError.textContent = '请输入口令';
        return;
    }
    redeemBtn.disabled = true;
    redeemBtn.textContent = '验证中...';
    try {
        var res = await fetch('/api/redeem', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });
        var result = await res.json();
        if (result.token) {
            window.location.href = '/setup?token=' + result.token;
        } else if (result.error) {
            redeemError.textContent = result.error;
        } else {
            redeemError.textContent = '验证失败，请重试';
        }
    } catch (e) {
        redeemError.textContent = '网络错误，请重试';
    } finally {
        redeemBtn.disabled = false;
        redeemBtn.textContent = '开始体验';
    }
}

// === 账号登录/注册 ===
var nicknameInput = document.getElementById('nicknameInput');
var passwordInput = document.getElementById('passwordInput');
var authBtn = document.getElementById('authBtn');
var authToggle = document.getElementById('authToggle');
var authError = document.getElementById('authError');
var isRegisterMode = false;

// 自动登录：检查 localStorage 中的 token
(function() {
    try {
        var savedToken = localStorage.getItem('account_token');
        if (savedToken) {
            // 先验证 token 是否有效
            fetch('/api/recharge/status?token=' + savedToken).then(function(r) {
                // 用 setup 页面验证：如果 302 说明 token 失效
                return fetch('/setup?token=' + savedToken, {redirect: 'manual'});
            }).then(function(r) {
                if (r.type === 'opaqueredirect' || r.status === 302 || r.redirected) {
                    // token 失效，清除
                    localStorage.removeItem('account_token');
                    localStorage.removeItem('account_nickname');
                    localStorage.removeItem('account_api_key');
                } else {
                    window.location.href = '/setup?token=' + savedToken;
                }
            }).catch(function() {
                // 网络错误，不自动跳转
            });
        }
    } catch(e) {}
})();

// === 注册开关检测 ===
var _registrationEnabled = true;

(function checkRegistrationStatus() {
    fetch('/api/registration-status').then(function(r) { return r.json(); }).then(function(data) {
        _registrationEnabled = data.enabled !== false;
        var toggleEl = document.getElementById('authToggle');
        if (toggleEl && !_registrationEnabled) {
            toggleEl.textContent = '注册已关闭';
            toggleEl.style.color = '#8E8E93';
            toggleEl.style.cursor = 'default';
            toggleEl.onclick = function() {};
        }
    }).catch(function() {});
})();

function toggleAuthMode() {
    if (!_registrationEnabled) {
        var authError = document.getElementById('authError');
        if (authError) authError.textContent = '注册已关闭，请联系管理员';
        return;
    }
    // 跳转到 TTS API 页面统一注册
    window.location.href = '/tts-api';
}

nicknameInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') passwordInput.focus();
});
passwordInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doAuth();
});

async function doAuth() {
    var nickname = nicknameInput.value.trim();
    var password = passwordInput.value;
    authError.textContent = '';

    if (!nickname) {
        authError.textContent = '请输入昵称';
        return;
    }
    if (!password) {
        authError.textContent = '请输入密码';
        return;
    }

    var url = isRegisterMode ? '/api/account/register' : '/api/account/login';
    authBtn.disabled = true;
    authBtn.textContent = isRegisterMode ? '注册中...' : '登录中...';

    try {
        var res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nickname: nickname, password: password })
        });
        var result = await res.json();
        if (result.token) {
            // 保存到 localStorage（持久登录）
            try {
                localStorage.setItem('account_token', result.token);
                localStorage.setItem('account_nickname', result.nickname);
                localStorage.setItem('account_api_key', result.api_key);
                // 保存密码用于设定加密（不存明文，存 hash 作为加密种子）
                localStorage.setItem('account_pw_seed', password);
            } catch(e) {}
            window.location.href = '/setup?token=' + result.token;
        } else if (result.error) {
            authError.textContent = result.error;
        } else {
            authError.textContent = '操作失败，请重试';
        }
    } catch (e) {
        authError.textContent = '网络错误，请重试';
    } finally {
        authBtn.disabled = false;
        authBtn.textContent = isRegisterMode ? '注册' : '登录';
    }
}
