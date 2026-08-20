/* kikimiru Service Worker — 依存ゼロ・単一ファイル。
 *
 * 方針:
 * - シェル(player.html+manifest+アイコン)はプリキャッシュし、版数で世代管理する
 * - ナビゲーションは network-first(3秒タイムアウト)→ キャッシュのシェルを返す
 *   (クエリルーティングのためシェル1枚で全画面を復元できる)
 * - GETのAPI・deck.json・content.json・カバー画像は network-first → キャッシュfallback
 *   (オフライン時は最後に取得したスナップショットで書棚を表示する)
 * - 音声と Range 付き要求は、ネットワークが生きている間は一切触らず素通しする。
 *   Cache Storage は 200 全体しか保持できず、Range→206 の自前合成の不備は
 *   オンライン再生ごと壊すため(iOS Safariが特に厳格)
 * - ブック単位オフライン保存(kikimiru-book-* キャッシュ): ネットワーク不達時に限り、
 *   保存済みの 200 完全体から要求範囲を切り出して 206 を合成して返す。
 *   iOS Safari は再生開始時の Range プローブに正しい 206 を返せないと
 *   再生自体を開始しないため、200 をそのまま返す実装では成立しない
 * - 認証応答(401)はキャッシュに入れない。キャッシュ済み応答には X-Kikimiru-Cache: 1 を
 *   付けて返し、クライアントがオフライン表示の告知に使う
 */
"use strict";

// UI(player.html)やシェル部品を更新したら必ずこの版数を上げる。
// activate で旧世代のキャッシュを削除するため、端末側の古いシェルが確実に入れ替わる。
const CACHE_VERSION = "kikimiru-v5";
// ブック単位オフライン保存のキャッシュ名接頭辞。シェルの世代管理とは独立した
// 名前空間で、activate のクリーンアップ対象から除外する(シェル更新で
// ダウンロード済みの音声が消えないように)
const BOOK_CACHE_PREFIX = "kikimiru-book-";
const SHELL = [
  "/web/player.html",
  "/web/manifest.webmanifest",
  "/web/icon-32.png",
  "/web/icon-192.png",
  "/web/icon-512.png",
  "/web/icon-maskable-512.png",
  "/web/apple-touch-icon.png",
];
const NETWORK_TIMEOUT_MS = 3000;

self.addEventListener("install", (event) => {
  // 新しいSWを待機させず即座に有効化する。self-hostの単一ユーザー用途では
  // 「古いシェルが次回起動まで残る」不便の方が大きい
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys
        .filter((k) => k !== CACHE_VERSION && !k.startsWith(BOOK_CACHE_PREFIX))
        .map((k) => caches.delete(k)))
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

/** Rangeヘッダを解析して {start, end} を返す。不正・範囲外は null(=416相当)。 */
function parseRange(header, size) {
  const m = /^bytes=(\d*)-(\d*)$/.exec(header || "");
  if (!m || (m[1] === "" && m[2] === "")) return null;
  let start, end;
  if (m[1] === "") {
    // suffix形式 bytes=-N(末尾Nバイト)
    const n = parseInt(m[2], 10);
    if (!n) return null;
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = parseInt(m[1], 10);
    end = m[2] === "" ? size - 1 : Math.min(parseInt(m[2], 10), size - 1);
  }
  if (start >= size || start > end) return null;
  return { start, end };
}

/** オンライン時はサーバの206をそのまま。不達時のみ保存済み200完全体から206を合成する。
 *  Blob.slice は遅延評価でコピーを伴わないため、大きな音声でもメモリ安全。 */
async function rangeWithOfflineFallback(req) {
  try {
    return await fetch(req);
  } catch (e) {
    const full = await caches.match(req.url);
    if (!full) return new Response("offline", { status: 503, statusText: "Offline" });
    const blob = await full.blob();
    const range = parseRange(req.headers.get("Range"), blob.size);
    if (!range) {
      return new Response(null, {
        status: 416,
        headers: { "Content-Range": `bytes */${blob.size}` },
      });
    }
    return new Response(blob.slice(range.start, range.end + 1), {
      status: 206,
      headers: {
        "Content-Type": full.headers.get("Content-Type") || "application/octet-stream",
        "Content-Range": `bytes ${range.start}-${range.end}/${blob.size}`,
        "Content-Length": String(range.end - range.start + 1),
        "Accept-Ranges": "bytes",
      },
    });
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;               // 書き込み系は素通し(失敗はクライアントが再送)

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const p = url.pathname;

  // Range要求: オンライン時は従来どおりサーバの206を使う。ブック配下だけ
  // ネットワーク不達時のフォールバック(保存済みからの206合成)を挟む
  if (req.headers.get("Range")) {
    if (p.startsWith("/books/")) {
      event.respondWith(rangeWithOfflineFallback(req));
    }
    return;
  }

  // ナビゲーション: シェル1枚を ignoreSearch で返す(クエリルーティング)
  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, "/web/player.html", NETWORK_TIMEOUT_MS));
    return;
  }

  // 音声など再生系メディアはキャッシュに書き込まない(従来どおり)。
  // ただしブック配下は、ネットワーク不達時に保存済みの完全体で応答する
  const audioExts = [".mp3", ".m4a", ".m4b", ".opus", ".ogg", ".flac", ".wav"];
  if (audioExts.some((ext) => p.toLowerCase().endsWith(ext))) {
    if (p.startsWith("/books/")) {
      event.respondWith(fetch(req).catch(() =>
        caches.match(req.url).then((cached) =>
          cached || new Response("offline", { status: 503, statusText: "Offline" }))));
    }
    return;
  }

  // シェル部品・API・ブックの構造/カバーは network-first
  const cacheable =
    p.startsWith("/web/") ||
    p.startsWith("/api/") ||
    (p.startsWith("/books/") && (p.endsWith(".json") || [".jpg", ".jpeg", ".png", ".webp"].some((e) => p.toLowerCase().endsWith(e))));
  if (!cacheable) return;

  // APIはクエリ込みでキャッシュキーにする(library=毎のスナップショット)
  event.respondWith(networkFirst(req, req.url, NETWORK_TIMEOUT_MS));
});
