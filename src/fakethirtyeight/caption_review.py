"""Build a local HTML report for reviewing image caption choices."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from fakethirtyeight.caption import CAPTIONS_FILE, infer_caption_category
from fakethirtyeight.images import IMAGE_LOG, IMAGE_REFS_FILE
from fakethirtyeight.paths import DATA_DIR

REVIEW_FILE = DATA_DIR / "image_caption_review.html"


@dataclass(slots=True, frozen=True)
class ReviewRow:
    identifier: str
    ai_category: str
    ai_title: str
    ai_description: str
    ai_text: str
    caption_status: str
    caption_error: str
    source_category: str
    alt: str
    caption: str
    canonical_url: str
    file_path: str
    content_type: str
    bytes: str
    fetched_via: str
    source_count: int
    article_file: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _first_nonempty(rows: list[dict[str, str]], key: str) -> str:
    for row in rows:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_category(rows: list[dict[str, str]]) -> str:
    counts = Counter((row.get("category") or "").strip() for row in rows)
    counts.pop("", None)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _is_transient_error(row: dict[str, str]) -> bool:
    """Return True for retry-environment errors that are not classification signal."""
    error = (row.get("error") or "").lower()
    return any(
        phrase in error
        for phrase in (
            "nodename nor servname provided",
            "name or service not known",
            "temporary failure in name resolution",
        )
    )


def _rows(
    *,
    captions_path: Path,
    image_log_path: Path,
    refs_path: Path,
) -> list[ReviewRow]:
    refs_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for ref in _read_csv(refs_path):
        ident = ref.get("identifier") or ""
        if ident:
            refs_by_id[ident].append(ref)

    downloads = {
        row.get("identifier") or "": row
        for row in _read_csv(image_log_path)
        if row.get("identifier")
    }

    captions_by_id: dict[str, dict[str, str]] = {}
    for caption in _read_csv(captions_path):
        ident = caption.get("identifier") or ""
        if not ident:
            continue
        # The caption job is append-only so failed rows can later be repaired.
        # Show the latest successful row when one exists. If an image has no
        # success yet, prefer the latest meaningful model/input error over a
        # later transient retry-environment error.
        current = captions_by_id.get(ident)
        if (
            (caption.get("status") or "") == "ok"
            or not current
            or (
                (current.get("status") or "") != "ok"
                and _is_transient_error(current)
                and not _is_transient_error(caption)
            )
        ):
            captions_by_id[ident] = caption
        elif (
            (current.get("status") or "") != "ok"
            and not _is_transient_error(current)
            and _is_transient_error(caption)
        ):
            continue
        elif (current.get("status") or "") != "ok":
            captions_by_id[ident] = caption

    out: list[ReviewRow] = []
    for ident, caption in captions_by_id.items():
        refs = refs_by_id.get(ident, [])
        download = downloads.get(ident, {})
        out.append(
            ReviewRow(
                identifier=ident,
                ai_category=infer_caption_category(
                    caption.get("ai_category") or "",
                    title=caption.get("ai_title") or "",
                    description=caption.get("ai_description") or "",
                    text=caption.get("ai_text") or "",
                ),
                ai_title=caption.get("ai_title") or "",
                ai_description=caption.get("ai_description") or "",
                ai_text=caption.get("ai_text") or "",
                caption_status=caption.get("status") or "",
                caption_error=caption.get("error") or "",
                source_category=_source_category(refs),
                alt=_first_nonempty(refs, "alt"),
                caption=_first_nonempty(refs, "caption"),
                canonical_url=download.get("canonical_url")
                or _first_nonempty(refs, "canonical_url"),
                file_path=download.get("file_path") or "",
                content_type=download.get("content_type") or "",
                bytes=download.get("bytes") or "",
                fetched_via=download.get("fetched_via") or "",
                source_count=len(refs),
                article_file=_first_nonempty(refs, "article_file"),
            )
        )
    return out


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


def _html_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _html_text(value: str) -> str:
    return html.escape(value, quote=False)


def _json_script(value: object) -> str:
    """Serialize data safely inside an inline JSON script tag."""
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _summary(rows: list[ReviewRow]) -> str:
    statuses = Counter(row.caption_status or "(blank)" for row in rows)
    categories = Counter(row.ai_category or "(blank)" for row in rows)
    return json.dumps(
        {
            "rows": len(rows),
            "statuses": dict(statuses.most_common()),
            "categories": dict(categories.most_common()),
        },
        sort_keys=True,
    )


def _row_payload(row: ReviewRow, *, out_path: Path) -> dict[str, str | int]:
    image_src = _rel(row.file_path, from_path=out_path)
    article_href = _rel(row.article_file, from_path=out_path)
    return {
        "id": row.identifier,
        "category": row.ai_category,
        "title": row.ai_title,
        "description": row.ai_description,
        "text": row.ai_text,
        "status": row.caption_status,
        "error": row.caption_error,
        "sourceCategory": row.source_category,
        "alt": row.alt,
        "caption": row.caption,
        "canonicalUrl": row.canonical_url,
        "imageSrc": image_src,
        "contentType": row.content_type,
        "bytes": row.bytes,
        "fetchedVia": row.fetched_via,
        "sourceCount": row.source_count,
        "articleHref": article_href,
    }


def _render(rows: list[ReviewRow], out_path: Path) -> str:
    category_options = sorted({row.ai_category for row in rows if row.ai_category})
    status_options = sorted({row.caption_status for row in rows if row.caption_status})
    payload = [_row_payload(row, out_path=out_path) for row in rows]

    category_select = "\n".join(
        f'<option value="{_html_attr(category)}">{_html_text(category)}</option>'
        for category in category_options
    )
    status_select = "\n".join(
        f'<option value="{_html_attr(status)}">{_html_text(status)}</option>'
        for status in status_options
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Image Caption Review</title>
  <style>
    :root {{
      color-scheme: light;
      --border: #d8d5ce;
      --ink: #222;
      --muted: #68645e;
      --bg: #fbfaf7;
      --panel: #fff;
      --accent: #005ea8;
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
      background: rgba(251, 250, 247, 0.96);
      padding: 16px 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 170px 130px;
      gap: 10px;
      max-width: 980px;
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
      padding: 18px 20px 40px;
    }}
    .meta {{
      color: var(--muted);
      margin: 0 0 14px;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-top: 16px;
    }}
    button {{
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      color: var(--accent);
      cursor: pointer;
      font: inherit;
      padding: 8px 12px;
    }}
    button:disabled {{
      color: var(--muted);
      cursor: default;
      opacity: 0.7;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
      gap: 14px;
    }}
    .card {{
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
    }}
    .thumb {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 150px;
      border: 1px solid var(--border);
      background: #f3f1ec;
    }}
    .thumb img {{
      display: block;
      max-width: 100%;
      max-height: 220px;
      object-fit: contain;
    }}
    .kicker {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    .kicker span {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 7px;
      background: #faf8f1;
    }}
    h2 {{
      margin: 8px 0 6px;
      font-size: 17px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .desc {{
      margin: 0 0 10px;
    }}
    dl {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px 12px;
      margin: 0 0 10px;
    }}
    dt {{
      color: var(--muted);
      font-size: 12px;
    }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    details {{
      margin-top: 8px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 180px;
      overflow: auto;
      border: 1px solid var(--border);
      background: #faf8f1;
      padding: 8px;
    }}
    .links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 10px 0 0;
    }}
    a {{
      color: var(--accent);
    }}
    @media (max-width: 640px) {{
      .controls {{
        grid-template-columns: 1fr;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .card {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Image Caption Review</h1>
    <div class="controls">
      <input id="q" type="search" placeholder="Search titles, text, URLs, identifiers">
      <select id="category">
        <option value="">All categories</option>
        {category_select}
      </select>
      <select id="status">
        <option value="">All statuses</option>
        {status_select}
      </select>
    </div>
  </header>
  <main>
    <p class="meta"><span id="rendered-count">0</span> of <span id="visible-count">{len(rows)}</span> matching rows loaded. Summary: <code>{_html_text(_summary(rows))}</code></p>
    <section class="grid" id="cards" aria-live="polite"></section>
    <div class="actions">
      <button id="load-more" type="button">Load more</button>
      <span class="meta" id="load-status"></span>
    </div>
  </main>
  <script type="application/json" id="review-data">{_json_script(payload)}</script>
  <script>
    const BATCH_SIZE = 120;
    const q = document.getElementById("q");
    const category = document.getElementById("category");
    const status = document.getElementById("status");
    const cards = document.getElementById("cards");
    const renderedCount = document.getElementById("rendered-count");
    const visibleCount = document.getElementById("visible-count");
    const loadMore = document.getElementById("load-more");
    const loadStatus = document.getElementById("load-status");
    const rows = JSON.parse(document.getElementById("review-data").textContent);
    let filteredRows = rows;
    let rendered = 0;

    function escapeHtml(value) {{
      return String(value || "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }}[char]));
    }}

    function attr(value) {{
      return escapeHtml(value);
    }}

    function link(href, label) {{
      if (!href) return "";
      return `<a href="${{attr(href)}}" target="_blank" rel="noreferrer">${{escapeHtml(label)}}</a>`;
    }}

    function searchableText(row) {{
      if (!row.search) {{
        row.search = [
          row.id,
          row.category,
          row.title,
          row.description,
          row.text,
          row.sourceCategory,
          row.alt,
          row.caption,
          row.canonicalUrl,
        ].join(" ").toLowerCase();
      }}
      return row.search;
    }}

    function cardHtml(row) {{
      const title = row.title || row.id;
      const imageAlt = row.description || row.alt || row.id;
      return `
      <article class="card" data-id="${{attr(row.id)}}" data-category="${{attr(row.category)}}" data-status="${{attr(row.status)}}">
        <a class="thumb" href="${{attr(row.imageSrc)}}" target="_blank" rel="noreferrer">
          <img src="${{attr(row.imageSrc)}}" alt="${{attr(imageAlt)}}" loading="lazy" decoding="async">
        </a>
        <div class="body">
          <div class="kicker">
            <span>${{escapeHtml(row.category || "uncategorized")}}</span>
            <span>${{escapeHtml(row.status || "unknown")}}</span>
            <span>${{escapeHtml(row.sourceCategory || "no source category")}}</span>
          </div>
          <h2>${{escapeHtml(title)}}</h2>
          <p class="desc">${{escapeHtml(row.description)}}</p>
          <dl>
            <div><dt>Identifier</dt><dd><code>${{escapeHtml(row.id)}}</code></dd></div>
            <div><dt>Source count</dt><dd>${{escapeHtml(row.sourceCount)}}</dd></div>
            <div><dt>Fetched via</dt><dd>${{escapeHtml(row.fetchedVia)}}</dd></div>
            <div><dt>Content type</dt><dd>${{escapeHtml(row.contentType)}}</dd></div>
            <div><dt>Bytes</dt><dd>${{escapeHtml(row.bytes)}}</dd></div>
          </dl>
          <details>
            <summary>Extracted text</summary>
            <pre>${{escapeHtml(row.text)}}</pre>
          </details>
          <details>
            <summary>Original metadata</summary>
            <p><strong>Alt:</strong> ${{escapeHtml(row.alt)}}</p>
            <p><strong>Caption:</strong> ${{escapeHtml(row.caption)}}</p>
            <p><strong>Error:</strong> ${{escapeHtml(row.error)}}</p>
          </details>
          <p class="links">
            ${{link(row.canonicalUrl, "canonical image")}}
            ${{link(row.articleHref, "article snapshot")}}
          </p>
        </div>
      </article>`;
    }}

    function updateLoadState() {{
      renderedCount.textContent = rendered;
      visibleCount.textContent = filteredRows.length;
      const remaining = filteredRows.length - rendered;
      loadMore.disabled = remaining <= 0;
      loadMore.hidden = filteredRows.length <= BATCH_SIZE;
      loadStatus.textContent = remaining > 0 ? `${{remaining}} matching rows not loaded yet` : "";
    }}

    function renderNextBatch() {{
      const nextRows = filteredRows.slice(rendered, rendered + BATCH_SIZE);
      if (nextRows.length) {{
        cards.insertAdjacentHTML("beforeend", nextRows.map(cardHtml).join(""));
        rendered += nextRows.length;
      }}
      updateLoadState();
    }}

    function applyFilters() {{
      const needle = q.value.trim().toLowerCase();
      const categoryValue = category.value;
      const statusValue = status.value;
      filteredRows = rows.filter((row) => {{
        const matchesSearch = !needle || searchableText(row).includes(needle);
        const matchesCategory = !categoryValue || row.category === categoryValue;
        const matchesStatus = !statusValue || row.status === statusValue;
        return matchesSearch && matchesCategory && matchesStatus;
      }});
      rendered = 0;
      cards.replaceChildren();
      renderNextBatch();
    }}

    q.addEventListener("input", applyFilters);
    category.addEventListener("change", applyFilters);
    status.addEventListener("change", applyFilters);
    loadMore.addEventListener("click", renderNextBatch);
    renderNextBatch();
  </script>
</body>
</html>
"""


def build_review(
    *,
    captions_path: Path = CAPTIONS_FILE,
    image_log_path: Path = IMAGE_LOG,
    refs_path: Path = IMAGE_REFS_FILE,
    out_path: Path = REVIEW_FILE,
) -> int:
    """Write a local HTML review page and return the number of rows."""
    rows = _rows(
        captions_path=captions_path,
        image_log_path=image_log_path,
        refs_path=refs_path,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render(rows, out_path), encoding="utf-8")
    return len(rows)
