"""Vision-based classification of ambiguous images.

Some images — especially the timestamped ``screen-shot-…`` files —
don't have meaningful filenames or alt text, so we can't tell from
metadata alone whether they're charts, chats, or game UIs. This
module sends each one through Claude's vision API to get:

* a content category (``chart``, ``map``, ``table``, ``chart-screenshot``,
  ``chat``, ``social-media``, ``ui-screenshot``, ``photo``, ``other``)
* a one-sentence description (becomes alt text on the IA item)
* a concise suggested title

Results are written to ``data/image_captions.csv`` keyed by identifier
so :mod:`ia_image_upload` can join them in and override the
filename-derived category for items in scope.

Auth: ``ANTHROPIC_API_KEY`` env var. Resumable: identifiers already in
the captions CSV are skipped.
"""

from __future__ import annotations

import base64
import csv
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import anthropic
from anthropic import APIStatusError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.contrib.concurrent import thread_map

from fakethirtyeight.images import IMAGE_LOG, IMAGE_REFS_FILE
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

CAPTIONS_FILE = DATA_DIR / "image_captions.csv"

CAPTION_FIELDS = (
    "identifier",
    "ai_category",
    "ai_description",
    "ai_title",
    "model",
    "status",
    "error",
)

#: Default model — Sonnet is the sweet spot for visual classification.
#: Override with ``--model`` on the CLI.
DEFAULT_MODEL = "claude-sonnet-4-5"

#: Categories the model is allowed to return. Constrained list keeps
#: the downstream filter logic deterministic.
ALLOWED_CATEGORIES = (
    "chart",  # data visualization rendered as an image
    "map",  # geographic data visualization
    "table",  # tabular data presented as an image
    "chart-screenshot",  # screenshot of a chart from another source
    "chat",  # screenshot of a chat / IM / Slack / DM thread
    "social-media",  # screenshot of a tweet / post / Reddit / etc.
    "ui-screenshot",  # screenshot of a website or app UI (not data)
    "photo",  # a photograph
    "illustration",  # illustration / drawing / cartoon
    "other",  # none of the above
)

#: Categories that count as "in scope" for archival upload.
IN_SCOPE_AI_CATEGORIES = frozenset(["chart", "map", "table", "chart-screenshot"])


_PROMPT = """\
You're classifying an image extracted from FiveThirtyEight, the data
journalism website. The mission is to archive only charts, maps, and
data visualizations — illustrations, photos, chat screenshots, and UI
screenshots are out of scope.

Look at the image and respond with a single JSON object on one line
with these exact keys:

  "category":    one of: {categories}
  "description": one-sentence plain-text description of the image
                 (no markdown, suitable as <img alt> text, ≤200 chars)
  "title":       a concise display title for an archive.org item
                 (≤80 chars; if the image is a chart, prefer the
                 chart's own title text)

Return ONLY the JSON. No prose, no markdown fences.\
""".format(categories=", ".join(ALLOWED_CATEGORIES))


@dataclass(slots=True, frozen=True)
class CaptionResult:
    identifier: str
    ai_category: str = ""
    ai_description: str = ""
    ai_title: str = ""
    model: str = ""
    status: str = "ok"
    error: str = ""


def _detect_media_type(path: Path) -> str:
    """Pick the IANA media type Claude expects for the image source."""
    ext = path.suffix.lower()
    by_ext = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return by_ext.get(ext, "image/jpeg")


_JSON_RX = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(text: str) -> dict[str, str]:
    """Pull the JSON object out of the model's response."""
    text = text.strip()
    if not text.startswith("{"):
        m = _JSON_RX.search(text)
        if m:
            text = m.group(0)
    return json.loads(text)


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    reraise=True,
)
def _classify_one(
    client: anthropic.Anthropic, image_path: Path, *, model: str
) -> dict[str, str]:
    """Send one image to Claude. Returns the parsed JSON response."""
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    msg = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _detect_media_type(image_path),
                            "data": data,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )
    raw = "".join(block.text for block in msg.content if block.type == "text")
    return _parse_response(raw)


def _normalize_category(raw: str) -> str:
    """Clamp the model's category to the allow-list."""
    norm = (raw or "").strip().lower()
    if norm in ALLOWED_CATEGORIES:
        return norm
    return "other"


def _load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "ok" and row.get("identifier"):
                out.add(row["identifier"])
    return out


def _select_targets(
    refs_path: Path,
    image_log_path: Path,
    *,
    only_screenshots: bool,
) -> list[tuple[str, Path]]:
    """Build ``(identifier, local_file_path)`` for everything to caption.

    Filters to screenshot category by default; pass ``only_screenshots=False``
    to caption every downloaded image.
    """
    # Build identifier → category lookup from the references CSV
    cat_by_id: dict[str, str] = {}
    with refs_path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ident = r.get("identifier") or ""
            if ident and ident not in cat_by_id:
                cat_by_id[ident] = r.get("category") or ""

    targets: list[tuple[str, Path]] = []
    with image_log_path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("status") or "") != "ok":
                continue
            ident = r.get("identifier") or ""
            fp_rel = (r.get("file_path") or "").strip()
            if not ident or not fp_rel:
                continue
            if only_screenshots and cat_by_id.get(ident) != "screenshot":
                continue
            fp = DATA_DIR.parent / fp_rel
            if fp.exists():
                targets.append((ident, fp))
    return targets


def caption_images(
    *,
    workers: int = 4,
    limit: int | None = None,
    only_screenshots: bool = True,
    model: str = DEFAULT_MODEL,
    refs_path: Path = IMAGE_REFS_FILE,
    image_log_path: Path = IMAGE_LOG,
    out_path: Path = CAPTIONS_FILE,
) -> tuple[int, int]:
    """Classify a subset of downloaded images using Claude vision.

    Returns ``(captioned, failed)``. Resumable via ``out_path``.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        msg = "Set ANTHROPIC_API_KEY first."
        raise RuntimeError(msg)

    ensure_dirs()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    targets = _select_targets(
        refs_path, image_log_path, only_screenshots=only_screenshots
    )
    done = _load_done(out_path)
    pending = [t for t in targets if t[0] not in done]
    log.info(
        "%d candidate images; %d already captioned; %d to process",
        len(targets),
        len(done),
        len(pending),
    )
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, processing %d", limit, len(pending))

    if not pending:
        return (0, 0)

    write_header = not out_path.exists()
    write_lock = threading.Lock()
    client = anthropic.Anthropic()

    with out_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAPTION_FIELDS)
        if write_header:
            writer.writeheader()
            fh.flush()

        def _process(item: tuple[str, Path]) -> int:
            ident, fp = item
            try:
                parsed = _classify_one(client, fp, model=model)
                result = CaptionResult(
                    identifier=ident,
                    ai_category=_normalize_category(parsed.get("category", "")),
                    ai_description=str(parsed.get("description", "")).strip()[:300],
                    ai_title=str(parsed.get("title", "")).strip()[:120],
                    model=model,
                    status="ok",
                )
            except Exception as exc:  # noqa: BLE001
                result = CaptionResult(
                    identifier=ident,
                    model=model,
                    status="error",
                    error=repr(exc)[:200],
                )
                log.warning("caption failed: %s — %s", ident, exc)
            with write_lock:
                writer.writerow(
                    {
                        "identifier": result.identifier,
                        "ai_category": result.ai_category,
                        "ai_description": result.ai_description,
                        "ai_title": result.ai_title,
                        "model": result.model,
                        "status": result.status,
                        "error": result.error,
                    }
                )
                fh.flush()
            return 1 if result.status == "ok" else 0

        outcomes = thread_map(
            _process,
            pending,
            max_workers=workers,
            desc="captioning",
            unit="img",
        )

    n_ok = sum(outcomes)
    return n_ok, len(pending) - n_ok
