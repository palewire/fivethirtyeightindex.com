"""Build a local HTML report for reviewing rendered HTML graphics."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fakethirtyeight.ai2html import (
    AI2HTML_REFS_FILE,
    AI2HTML_RENDER_LOG,
    _collect_refs,
    _mostly_blank,
)
from fakethirtyeight.embeds import EMBED_REFS_FILE, EMBED_RENDER_LOG
from fakethirtyeight.paths import DATA_DIR

REVIEW_FILE = DATA_DIR / "ai2html_render_review.html"


def _read_latest_render_rows(path: Path) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    if not path.exists():
        return latest
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("canonical_url") or "").strip()
            if canonical:
                latest[canonical] = row
    return latest


def _rel(path: str, *, from_path: Path) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_absolute():
        p = DATA_DIR.parent / p
    try:
        return p.relative_to(from_path.parent).as_posix()
    except ValueError:
        return p.as_posix()


def _json_script(value: object) -> str:
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _payload_for(
    *,
    label: str,
    refs_path: Path,
    render_log_path: Path,
    out_path: Path,
) -> list[dict[str, str | bool]]:
    renders = _read_latest_render_rows(render_log_path)
    rows: list[dict[str, str | bool]] = []
    for ref in _collect_refs(refs_path):
        canonical = ref["canonical_url"]
        render = renders.get(canonical, {})
        render_path = render.get("render_path") or ""
        full_render_path = DATA_DIR.parent / render_path if render_path else None
        valid = (
            bool(render_path)
            and bool(full_render_path)
            and full_render_path.exists()
            and not _mostly_blank(full_render_path)
        )
        rows.append(
            {
                "id": ref["identifier"],
                "collection": label,
                "canonicalUrl": canonical,
                "articleUrl": ref.get("article_url") or "",
                "articleFile": ref.get("article_file") or "",
                "title": ref.get("title") or "",
                "caption": ref.get("caption") or "",
                "kind": ref.get("kind") or "",
                "childId": ref.get("child_id") or "",
                "renderSrc": _rel(render_path, from_path=out_path) if valid else "",
                "sourceHtml": _rel(render.get("file_path") or "", from_path=out_path),
                "status": "ok" if valid else (render.get("status") or "missing"),
                "error": "" if valid else (render.get("error") or ""),
                "width": render.get("width") or "",
                "height": render.get("height") or "",
                "bytes": render.get("bytes") or "",
                "valid": valid,
            }
        )
    return rows


def _payload(out_path: Path) -> list[dict[str, str | bool]]:
    return [
        *_payload_for(
            label="ai2html",
            refs_path=AI2HTML_REFS_FILE,
            render_log_path=AI2HTML_RENDER_LOG,
            out_path=out_path,
        ),
        *_payload_for(
            label="embed",
            refs_path=EMBED_REFS_FILE,
            render_log_path=EMBED_RENDER_LOG,
            out_path=out_path,
        ),
    ]


def _render(rows: list[dict[str, str | bool]]) -> str:
    ok_count = sum(1 for row in rows if row["valid"])
    ai2html_count = sum(1 for row in rows if row["collection"] == "ai2html")
    embed_count = sum(1 for row in rows if row["collection"] == "embed")
    payload = _json_script(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HTML Graphic Render Review</title>
  <style>
    :root {{
      --bg: #f8f7f4;
      --panel: #fff;
      --border: #d8d5ce;
      --ink: #222;
      --muted: #666;
      --accent: #005ea8;
      color-scheme: light;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--border);
      background: rgba(248, 247, 244, 0.96);
      padding: 16px 20px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 20px;
      line-height: 1.2;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) 160px 160px 160px;
      gap: 10px;
      max-width: 900px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 8px 10px;
    }}
    main {{
      padding: 20px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 16px;
      align-items: start;
    }}
    article {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: clip;
    }}
    .image {{
      display: flex;
      align-items: flex-start;
      justify-content: center;
      min-height: 220px;
      max-height: 620px;
      overflow: auto;
      border-bottom: 1px solid var(--border);
      background: #fff;
    }}
    .image img {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .missing {{
      width: 100%;
      min-height: 220px;
      display: grid;
      place-items: center;
      padding: 24px;
      color: #8a2500;
      text-align: center;
      background: #fff7f2;
    }}
    .body {{
      padding: 12px;
    }}
    h2 {{
      margin: 0 0 8px;
      font-size: 15px;
      line-height: 1.25;
    }}
    dl {{
      display: grid;
      grid-template-columns: 90px 1fr;
      gap: 5px 8px;
      margin: 0;
    }}
    dt {{
      color: var(--muted);
    }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    a {{
      color: var(--accent);
    }}
    .hidden {{
      display: none;
    }}
    @media (max-width: 760px) {{
      .controls {{
        grid-template-columns: 1fr;
      }}
      main {{
        padding: 12px;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>HTML Graphic Render Review</h1>
    <div class="summary">
      <span><strong id="visible-count">{ok_count}</strong> visible</span>
      <span>{ok_count} valid PNGs</span>
      <span>{len(rows) - ok_count} missing or blank</span>
      <span>{ai2html_count} ai2html</span>
      <span>{embed_count} embeds</span>
    </div>
    <div class="controls">
      <input id="search" type="search" placeholder="Search title, URL, article, identifier">
      <select id="collection">
        <option value="">All types</option>
        <option value="ai2html">ai2html</option>
        <option value="embed">Embeds</option>
      </select>
      <select id="status">
        <option value="">All statuses</option>
        <option value="ok">OK</option>
        <option value="error">Error</option>
        <option value="missing">Missing</option>
      </select>
      <select id="kind">
        <option value="">All kinds</option>
        <option value="iframe">Iframe</option>
        <option value="inline">Inline</option>
        <option value="pym">Pym</option>
        <option value="url">URL</option>
      </select>
    </div>
  </header>
  <main>
    <div id="grid" class="grid"></div>
  </main>
  <script id="rows" type="application/json">{payload}</script>
  <script>
    const rows = JSON.parse(document.getElementById('rows').textContent);
    const grid = document.getElementById('grid');
    const search = document.getElementById('search');
    const collectionFilter = document.getElementById('collection');
    const statusFilter = document.getElementById('status');
    const kindFilter = document.getElementById('kind');
    const visibleCount = document.getElementById('visible-count');

    function esc(value) {{
      return String(value || '').replace(/[&<>"']/g, (char) => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
      }}[char]));
    }}

    function card(row) {{
      const image = row.renderSrc
        ? `<div class="image"><a href="${{esc(row.renderSrc)}}"><img loading="lazy" src="${{esc(row.renderSrc)}}" alt=""></a></div>`
        : `<div class="missing">No valid PNG render<br>${{esc(row.error || row.status)}}</div>`;
      return `<article data-status="${{esc(row.status)}}" data-kind="${{esc(row.kind)}}">
        ${{image}}
        <div class="body">
          <h2>${{esc(row.title || row.id)}}</h2>
          <dl>
            <dt>Status</dt><dd>${{esc(row.status)}} ${{row.valid ? '' : esc(row.error)}}</dd>
            <dt>Type</dt><dd>${{esc(row.collection)}}</dd>
            <dt>Kind</dt><dd>${{esc(row.kind)}}</dd>
            <dt>Size</dt><dd>${{esc(row.width)}} x ${{esc(row.height)}} px</dd>
            <dt>Identifier</dt><dd>${{esc(row.id)}}</dd>
            <dt>Source</dt><dd><a href="${{esc(row.sourceHtml)}}">${{esc(row.sourceHtml)}}</a></dd>
            <dt>Canonical</dt><dd><a href="${{esc(row.canonicalUrl)}}">${{esc(row.canonicalUrl)}}</a></dd>
            <dt>Article</dt><dd><a href="${{esc(row.articleUrl)}}">${{esc(row.articleUrl)}}</a></dd>
          </dl>
        </div>
      </article>`;
    }}

    function matches(row) {{
      const q = search.value.trim().toLowerCase();
      const collection = collectionFilter.value;
      const status = statusFilter.value;
      const kind = kindFilter.value;
      if (collection && row.collection !== collection) return false;
      if (status && row.status !== status) return false;
      if (kind && row.kind !== kind) return false;
      if (!q) return true;
      return [row.id, row.collection, row.title, row.caption, row.canonicalUrl, row.articleUrl, row.articleFile]
        .join(' ')
        .toLowerCase()
        .includes(q);
    }}

    function render() {{
      const filtered = rows.filter(matches);
      visibleCount.textContent = String(filtered.length);
      grid.innerHTML = filtered.map(card).join('');
    }}

    search.addEventListener('input', render);
    collectionFilter.addEventListener('change', render);
    statusFilter.addEventListener('change', render);
    kindFilter.addEventListener('change', render);
    render();
  </script>
</body>
</html>
"""


def build_review(*, out_path: Path = REVIEW_FILE) -> int:
    rows = _payload(out_path)
    out_path.write_text(_render(rows), encoding="utf-8")
    return len(rows)
