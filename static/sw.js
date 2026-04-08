// Service Worker — AI Voice Call PWA
const CACHE_NAME = 'voice-call-v1';
const APP_SHELL = [
  '/',
  '/static/theme-ios.css',
  '/static/login.js',
  '/static/setup.js',
  '/static/call.js',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/manifest.json'
];

// 安装：预缓存 App Shell
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(APP_SHELL);
    })
  );
  self.skipWaiting();
});

// 激活：清理旧缓存
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(name) {
          return name !== CACHE_NAME;
        }).map(function(name) {
          return caches.delete(name);
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch：静态资源走缓存，API/SSE 请求透传
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // API 请求不缓存（包括 SSE 流式响应）
  if (url.pathname.startsWith('/api/')) {
    return; // 不拦截，浏览器正常发起请求
  }

  // SSE / EventSource 请求透传
  if (event.request.headers.get('Accept') === 'text/event-stream') {
    return;
  }

  // 静态资源：缓存优先，网络回退
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      if (cached) {
        // 后台更新缓存（stale-while-revalidate）
        fetch(event.request).then(function(response) {
          if (response && response.status === 200) {
            caches.open(CACHE_NAME).then(function(cache) {
              cache.put(event.request, response);
            });
          }
        }).catch(function() {});
        return cached;
      }
      return fetch(event.request).then(function(response) {
        // 只缓存同源的成功响应
        if (response && response.status === 200 && url.origin === self.location.origin) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      });
    })
  );
});
