/* kikimiru Service Worker — 依存ゼロ・単一ファイル。
 *
 * 方針(CP1承認済み設計B):
 * - シェル(player.html+manifest+アイコン)はプリキャッシュし、版数で世代管理する
 * - ナビゲーションは network-first(3秒タイムアウト)→ キャッシュのシェルを返す
 *   (クエリルーティングのためシェル1枚で全画面を復元できる)
 * - GETのAPI・deck.json・content.json・カバー画像は network-first → キャッシュfallback
 *   (オフライン時は最後に取得したスナップショットで書棚を表示する)
 * - 音声と Range 付き要求は一切キャッシュせず素通しする。Cache Storage は 200 全体しか
 *   保持できず、Range→206 の自前合成の不備はオンライン再生ごと壊すため(iOS Safariが特に厳格)
 * - 認証応答(401)はキャッシュに入れない。キャッシュ済み応答には X-Kikimiru-Cache: 1 を
 *   付けて返し、クライアントがオフライン表示の告知に使う
 */
"use strict";

const CACHE_VERSION = "kikimiru-v1";
const SHELL = [
  "/web/player.html",
  "/web/manifest.webmanifest",
  "/web/icon-192.png",
  "/web/icon-512.png",
  "/web/icon-maskable-512.png",
  "/web/apple-touch-icon.png",
];
const NETWORK_TIMEOUT_MS = 3000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "skipWaiting") self.skipWaiting();
});

/** キャッシュ由来だとクライアントに分かるようヘッダを足して返す */
function markCached(response) {
  if (!response) return response;
  const headers = new Headers(response.headers);
  headers.set("X-Kikimiru-Cache", "1");
  return response.arrayBuffer ? response.blob().then((body) =>
    new Response(body, { status: response.status, statusText: response.statusText, headers })
  ) : response;
}

function networkFirst(request, cacheKey, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      caches.match(cacheKey).then((cached) => {
        if (!settled && cached) { settled = true; resolve(markCached(cached)); }
      });
    }, timeoutMs);
    fetch(request).then((res) => {
      clearTimeout(timer);
      if (settled) return;
      // 成功応答のみキャッシュを更新(401/5xx等でスナップショットを潰さない)
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE_VERSION).then((cache) => cache.put(cacheKey, copy));
      }
      settled = true;
      resolve(res);
    }).catch(() => {
      clearTimeout(timer);
      if (settled) return;
      caches.match(cacheKey).then((cached) => {
        settled = true;
        if (cached) resolve(markCached(cached));
        else resolve(new Response("offline", { status: 503, statusText: "Offline" }));
      });
    });
  });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;               // 書き込み系は素通し(失敗はクライアントが再送)
  if (req.headers.get("Range")) return;           // Range要求は絶対に触らない(音声シーク)

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // ナビゲーション: シェル1枚を ignoreSearch で返す(クエリルーティング)
  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, "/web/player.html", NETWORK_TIMEOUT_MS));
    return;
  }

  const p = url.pathname;

  // 音声など再生系メディアは素通し(キャッシュしない)
  const audioExts = [".mp3", ".m4a", ".m4b", ".opus", ".ogg", ".flac", ".wav"];
  if (audioExts.some((ext) => p.toLowerCase().endsWith(ext))) return;

  // シェル部品・API・ブックの構造/カバーは network-first
  const cacheable =
    p.startsWith("/web/") ||
    p.startsWith("/api/") ||
    (p.startsWith("/books/") && (p.endsWith(".json") || [".jpg", ".jpeg", ".png", ".webp"].some((e) => p.toLowerCase().endsWith(e))));
  if (!cacheable) return;

  // APIはクエリ込みでキャッシュキーにする(library=毎のスナップショット)
  event.respondWith(networkFirst(req, req.url, NETWORK_TIMEOUT_MS));
});
