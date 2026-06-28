// Service Worker for offline caching
var CACHE_NAME = 'cryptoquant-v1';
var urlsToCache = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/dashboard.js',
  '/static/js/strategy.js',
  '/static/js/strategy_store.js',
  '/static/js/error_handler.js',
  '/static/js/strategy_guide.js',
  '/static/js/vendor/chart.umd.min.js',
  '/static/js/vendor/lightweight-charts.standalone.production.js'
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(urlsToCache);
    })
  );
});

self.addEventListener('fetch', function(event) {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.match(event.request).then(function(response) {
      return response || fetch(event.request).then(function(networkResponse) {
        if (networkResponse && networkResponse.status === 200) {
          var responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, responseClone);
          });
        }
        return networkResponse;
      }).catch(function() {
        return caches.match('/');
      });
    })
  );
});
