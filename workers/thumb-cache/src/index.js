const ARCHIVE_THUMBNAIL_BASE_URL = "https://archive.org/services/img";
const CACHE_TTL_SECONDS = 31_536_000;
const ERROR_TTL_SECONDS = 300;
const THUMBNAIL_PATH = /^\/([A-Za-z0-9_-]+)\/?$/;

function responseWithHeaders(response, headers) {
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function normalizedCacheKey(request) {
  const url = new URL(request.url);
  url.search = "";
  return new Request(url.toString(), { method: "GET" });
}

function thumbnailHeaders(sourceHeaders, cacheState) {
  const headers = new Headers(sourceHeaders);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", `public, max-age=${CACHE_TTL_SECONDS}, immutable`);
  headers.set("CDN-Cache-Control", `public, max-age=${CACHE_TTL_SECONDS}`);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("X-Thumb-Cache", cacheState);
  return headers;
}

export default {
  async fetch(request, _env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    const url = new URL(request.url);
    const match = url.pathname.match(THUMBNAIL_PATH);
    if (!match) {
      return new Response("Not found", { status: 404 });
    }

    const cache = caches.default;
    const cacheKey = normalizedCacheKey(request);
    const cached = await cache.match(cacheKey);
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("X-Thumb-Cache", "HIT");
      if (request.method === "HEAD") {
        return new Response(null, {
          status: cached.status,
          statusText: cached.statusText,
          headers,
        });
      }
      return responseWithHeaders(cached, headers);
    }

    const originUrl = `${ARCHIVE_THUMBNAIL_BASE_URL}/${match[1]}`;
    const originResponse = await fetch(originUrl, {
      cf: {
        cacheEverything: true,
        cacheTtl: CACHE_TTL_SECONDS,
      },
      headers: {
        Accept: "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*,*/*;q=0.8",
      },
    });

    if (!originResponse.ok) {
      const headers = new Headers(originResponse.headers);
      headers.set("Cache-Control", `public, max-age=${ERROR_TTL_SECONDS}`);
      headers.set("X-Thumb-Cache", "BYPASS");
      return new Response(request.method === "HEAD" ? null : originResponse.body, {
        status: originResponse.status,
        statusText: originResponse.statusText,
        headers,
      });
    }

    const contentType = originResponse.headers.get("Content-Type") || "";
    if (!contentType.toLowerCase().startsWith("image/")) {
      return new Response("Archive.org thumbnail response was not an image", {
        status: 502,
        headers: {
          "Cache-Control": `public, max-age=${ERROR_TTL_SECONDS}`,
          "Content-Type": "text/plain; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
          "X-Thumb-Cache": "BYPASS",
        },
      });
    }

    const headers = thumbnailHeaders(originResponse.headers, "MISS");
    const response = new Response(
      request.method === "HEAD" ? null : originResponse.body,
      {
        status: originResponse.status,
        statusText: originResponse.statusText,
        headers,
      },
    );

    if (request.method === "GET") {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }

    return response;
  },
};
