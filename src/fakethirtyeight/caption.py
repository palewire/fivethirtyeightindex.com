"""Vision-based classification of ambiguous images.

Some images — especially the timestamped ``screen-shot-…`` files —
don't have meaningful filenames or alt text, so we can't tell from
metadata alone whether they're charts, chats, or game UIs. This
module sends each one through an OpenAI-compatible LiteLLM vision endpoint to get:

* a content category (``chart``, ``map``, ``table``, ``chart-screenshot``,
  ``infographic``, ``chess-diagram``, ``diagram``,
  ``artistic-illustration``, ``chat``, ``social-media``, ``ui-screenshot``,
  ``photo``, ``other``)
* a one-sentence description (becomes alt text on the IA item)
* a concise suggested title
* any visible text extracted from the image

Results are written to ``data/image_captions.csv`` keyed by identifier
so :mod:`ia_image_upload` can join them in and override the
filename-derived category for items in scope.

Auth: ``LITELLM_API_KEY`` + ``LITELLM_BASE_URL`` env vars. Resumable:
identifiers already in the captions CSV are skipped.
"""

from __future__ import annotations

import base64
import csv
import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urljoin

import httpx
from PIL import Image
from tqdm.contrib.concurrent import thread_map

from fakethirtyeight.http import DEFAULT_TIMEOUT, make_ssl_context
from fakethirtyeight.images import IMAGE_LOG, IMAGE_REFS_FILE, _is_image_body
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

CAPTIONS_FILE = DATA_DIR / "image_captions.csv"
MODEL_INPUTS_DIR = DATA_DIR / "image_caption_inputs"

MAX_MODEL_INPUT_BYTES = 3_500_000
MAX_MODEL_INPUT_DIMENSION = 7_500
MODEL_RESIZE_MAX_SIDE = 4_096
MODEL_JPEG_QUALITIES = (85, 75, 65)
MODEL_RESIZE_MAX_SIDES = (4_096, 3_072, 2_048)
MAX_TEXT_CHARS = 500
STRICT_MAX_TEXT_CHARS = 250
MAX_CLASSIFY_ATTEMPTS = 4

CAPTION_FIELDS = (
    "identifier",
    "ai_category",
    "ai_description",
    "ai_title",
    "ai_text",
    "model",
    "status",
    "error",
)

#: Default model. Prefer ``LITELLM_MODEL`` so local config can choose
#: whatever the gateway exposes. Override with ``--model`` on the CLI.
DEFAULT_MODEL = os.environ.get("LITELLM_MODEL") or "gpt-4o-mini"

#: Categories the model is allowed to return. Constrained list keeps
#: the downstream filter logic deterministic.
ALLOWED_CATEGORIES = (
    "chart",  # data visualization rendered as an image
    "map",  # geographic data visualization
    "table",  # tabular data presented as an image
    "chart-screenshot",  # screenshot of a chart from another source
    "infographic",  # designed data graphic with numbers/icons/text
    "chess-diagram",  # chess board positions and move diagrams
    "diagram",  # explanatory / mathematical / technical diagram
    "artistic-illustration",  # editorial art that does not encode data
    "chat",  # screenshot of a chat / IM / Slack / DM thread
    "social-media",  # screenshot of a tweet / post / Reddit / etc.
    "ui-screenshot",  # screenshot of a website or app UI (not data)
    "photo",  # a photograph
    "illustration",  # legacy generic illustration label
    "other",  # none of the above
)

#: Categories that count as "in scope" for archival upload.
IN_SCOPE_AI_CATEGORIES = frozenset(
    [
        "artistic-illustration",
        "chart",
        "map",
        "table",
        "chart-screenshot",
        "infographic",
        "diagram",
        "chess-diagram",
    ]
)


_PROMPT = """\
You're classifying an image extracted from FiveThirtyEight, the data
journalism website. The mission is to archive charts, maps, data
visualizations, number/icon-driven infographics, and chess board
diagrams — photos, chat screenshots, and UI screenshots are out of
scope.

Use "infographic" for designed editorial graphics that communicate
data, rankings, quantities, distances, prices, or lowest/highest
comparisons using numbers, icons, illustrations, or large text instead
of conventional axes. Use "diagram" for explanatory, mathematical,
technical, puzzle, or schematic drawings that are not data graphics.
Use "artistic-illustration" for non-data editorial art, cartoons, or
decorative drawings that do not communicate a concrete data comparison
or explain a technical relationship. Avoid "illustration" unless none
of the more specific illustration categories fit.

Look at the image and respond with a single JSON object on one line
with these exact keys:

  "category":    one of: {categories}
  "description": one-sentence plain-text description of the image
                 (no markdown, suitable as <img alt> text, ≤200 chars)
  "title":       a concise display title for an archive.org item
                 (≤80 chars; if the image is a chart, prefer the
                 chart's own title text)
  "text":        all legible text visible in the image, preserving the
                 rough reading order, capped at {max_text_chars}
                 characters. Use an empty string if there is no
                 legible text. Do not summarize or invent text.

Return ONLY valid JSON. No prose, no markdown fences. Escape any
double quotes inside strings as \\". If the visible text is too long
to return safely as valid JSON, include the most important visible text
and omit the rest rather than returning broken JSON.\
""".format(categories=", ".join(ALLOWED_CATEGORIES), max_text_chars=MAX_TEXT_CHARS)

_STRICT_JSON_PROMPT = f"""\
Your previous response was invalid JSON. Return valid JSON only.
Use exactly the same schema, but cap "text" at {STRICT_MAX_TEXT_CHARS}
characters. Do not include unescaped double quotes inside any string.
Prefer omitting dense OCR text over returning invalid JSON.\
"""


@dataclass(slots=True, frozen=True)
class CaptionResult:
    identifier: str
    ai_category: str = ""
    ai_description: str = ""
    ai_title: str = ""
    ai_text: str = ""
    model: str = ""
    status: str = "ok"
    error: str = ""


def _detect_media_type(path: Path) -> str:
    """Pick the IANA media type the vision endpoint expects."""
    ext = path.suffix.lower()
    by_ext = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return by_ext.get(ext, "image/jpeg")


def _needs_prepared_input(path: Path) -> bool:
    """Return True when the model endpoint may reject the original image."""
    if path.stat().st_size > MAX_MODEL_INPUT_BYTES:
        return True
    try:
        with Image.open(path) as img:
            width, height = img.size
    except OSError:
        return False
    return max(width, height) > MAX_MODEL_INPUT_DIMENSION


def _model_input_is_acceptable(path: Path) -> bool:
    """Return True when a prepared file should pass model input constraints."""
    if not path.exists() or path.stat().st_size > MAX_MODEL_INPUT_BYTES:
        return False
    try:
        with Image.open(path) as img:
            width, height = img.size
    except OSError:
        return False
    return max(width, height) <= MAX_MODEL_INPUT_DIMENSION


def _rgb_with_white_background(img: Image.Image) -> Image.Image:
    """Convert possibly transparent images to RGB with a white background."""
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return bg.convert("RGB")
    return img.convert("RGB")


def _prepare_model_input(path: Path, *, identifier: str) -> Path:
    """Create a smaller model-only copy when the source image is too large.

    The original file remains the upload source of truth. Prepared
    inputs are deterministic per identifier so retries can reuse them.
    """
    if not _needs_prepared_input(path):
        return path

    MODEL_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = MODEL_INPUTS_DIR / f"{identifier}.jpg"
    source_mtime = path.stat().st_mtime
    if (
        out.exists()
        and out.stat().st_mtime >= source_mtime
        and _model_input_is_acceptable(out)
    ):
        return out

    with Image.open(path) as img:
        img.load()
        original = _rgb_with_white_background(img)
        for max_side in MODEL_RESIZE_MAX_SIDES:
            resized = original.copy()
            resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            for quality in MODEL_JPEG_QUALITIES:
                resized.save(out, format="JPEG", quality=quality, optimize=True)
                if _model_input_is_acceptable(out):
                    return out

    if _model_input_is_acceptable(out):
        return out
    msg = f"prepared model input is still too large: {out}"
    raise RuntimeError(msg)


def _litellm_url() -> str:
    base = (os.environ.get("LITELLM_BASE_URL") or "").strip()
    if not base:
        msg = "Set LITELLM_BASE_URL first."
        raise RuntimeError(msg)
    if base.rstrip("/").endswith("/chat/completions"):
        return base
    return urljoin(base.rstrip("/") + "/", "chat/completions")


def _litellm_headers() -> dict[str, str]:
    key = (os.environ.get("LITELLM_API_KEY") or "").strip()
    if not key:
        msg = "Set LITELLM_API_KEY first."
        raise RuntimeError(msg)
    user_agent = (os.environ.get("LITELLM_USER_AGENT") or "").strip()
    if not user_agent:
        msg = "Set LITELLM_USER_AGENT first."
        raise RuntimeError(msg)
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }


_JSON_RX = re.compile(r"\{.*\}", re.DOTALL)


class CaptionParseError(RuntimeError):
    """Raised when the model returns a response we cannot parse."""


def _parse_response(text: str) -> dict[str, str]:
    """Pull the JSON object out of the model's response."""
    text = text.strip()
    if not text.startswith("{"):
        m = _JSON_RX.search(text)
        if m:
            text = m.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Could not parse model JSON response: {text[:500]}"
        raise CaptionParseError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"Model response was not a JSON object: {text[:500]}"
        raise CaptionParseError(msg)
    return {str(k): str(v) for k, v in parsed.items()}


def _content_from_response(data: dict[str, object]) -> str:
    """Extract assistant text from an OpenAI-compatible response."""
    if data.get("error"):
        error = data["error"]
        if isinstance(error, Mapping):
            error_map = cast(Mapping[str, object], error)
            message_obj = error_map.get("message")
            message = str(message_obj or error_map)
        else:
            message = str(error)
        msg = f"LiteLLM error: {message}"
        raise RuntimeError(msg[:500])

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        msg = f"LiteLLM response missing choices: keys={sorted(data)}"
        raise RuntimeError(msg)
    first = choices[0]
    if not isinstance(first, dict):
        msg = "LiteLLM response choice is not an object"
        raise RuntimeError(msg)
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = first.get("text")
    if isinstance(text, str):
        return text
    msg = "LiteLLM response choice missing message content"
    raise RuntimeError(msg)


def _classification_payload(
    image_path: Path, *, model: str, strict_json: bool
) -> dict[str, object]:
    """Build the OpenAI-compatible vision classification request."""
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    prompt = _PROMPT
    if strict_json:
        prompt = f"{_PROMPT}\n\n{_STRICT_JSON_PROMPT}"
    return {
        "model": model,
        "max_tokens": 900 if not strict_json else 700,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{_detect_media_type(image_path)};base64,{data}"
                            )
                        },
                    },
                ],
            }
        ],
    }


def _classify_one(
    client: httpx.Client, image_path: Path, *, model: str
) -> dict[str, str]:
    """Send one image to a LiteLLM vision endpoint and parse JSON output."""
    last_exc: Exception | None = None
    for attempt in range(MAX_CLASSIFY_ATTEMPTS):
        strict_json = isinstance(last_exc, CaptionParseError)
        try:
            payload = _classification_payload(
                image_path, model=model, strict_json=strict_json
            )
            resp = client.post(_litellm_url(), json=payload)
            if resp.status_code in {429, 500, 502, 503, 504}:
                resp.raise_for_status()
            resp.raise_for_status()
            raw = _content_from_response(resp.json())
            return _parse_response(raw)
        except (httpx.HTTPError, CaptionParseError) as exc:
            last_exc = exc
            if attempt == MAX_CLASSIFY_ATTEMPTS - 1:
                raise
            time.sleep(min(60, 2 ** (attempt + 2)))

    msg = "classification retry loop exited without result"
    raise RuntimeError(msg)


def _normalize_category(raw: str) -> str:
    """Clamp the model's category to the allow-list."""
    norm = (raw or "").strip().lower()
    if norm in ALLOWED_CATEGORIES:
        return norm
    return "other"


def infer_caption_category(
    raw: str,
    *,
    title: str = "",
    description: str = "",
    text: str = "",
) -> str:
    """Normalize a model category and repair obvious category misses."""
    category = _normalize_category(raw)
    haystack = f"{title} {description} {text}".lower()

    if any(
        term in haystack
        for term in (
            "blank or dark image",
            "placeholder image",
            "failed image load",
            "blank image",
            "blank grid",
            "blank 10x10 grid",
            "blank name tag",
            "empty placeholder",
            "empty chart placeholder",
            "no data placeholder",
            "no data message",
            "no visible content",
            "no visible data",
            "no data or labels",
            "no labels or data",
            "no text or data content",
            "containing no data",
            "no data points plotted",
            "plain solid",
            "plain yellow grid pattern",
            "plain white circle",
            "green circle row",
            "mostly blank",
        )
    ):
        return "other"

    map_terms = (
        "choropleth",
        "geographic map",
        "map of",
        "u.s. map",
        "us map",
        "united states map",
        "county map",
    )
    if category == "infographic" and any(term in haystack for term in map_terms):
        return "map"

    table_terms = (
        "table",
        "ranked by awards points",
        "ranked by award points",
        "points from awards and nominations",
        "nominee points",
        "ranking of",
        "ranked list",
    )
    if category == "infographic" and any(term in haystack for term in table_terms):
        return "table"

    chart_terms = (
        "bar chart",
        "line chart",
        "scatter plot",
        "dot plot",
        "donut chart",
        "pie chart",
        "waffle chart",
        "heat map",
        "heat-map",
        "heatmap",
        "strike zone",
        "run value",
        "win probabilities",
        "probability grid",
    )
    if category == "infographic" and any(term in haystack for term in chart_terms):
        return "chart"

    if category in {"illustration", "other"} and any(
        term in haystack
        for term in (
            "infographic",
            "comparison",
            "price comparison",
            "ranking",
            "ranked",
            "percentages",
            "percentage",
            "least popular",
            "most popular",
            "lowest",
            "highest",
        )
    ):
        return "infographic"

    chess_terms = (
        "chess board",
        "chessboard",
        "chess diagram",
        "chess position",
        "chess piece",
        "chess pieces",
        "chess move",
        "chess endgame",
        "pawn promotion",
        "move arrow",
        "algebraic notation",
        "kasparov",
        "deep blue",
    )
    if any(term in haystack for term in chess_terms) and not any(
        term in haystack
        for term in (
            "chess-playing machine",
            "chess automaton",
            "mechanical turk",
            "movie still",
            "film still",
        )
    ):
        return "chess-diagram"

    diagram_terms = (
        "diagram",
        "schematic",
        "geometry",
        "geometric",
        "mathematical",
        "derivation",
        "formula",
        "puzzle",
        "maze",
        "grid",
        "lattice",
        "node",
        "network",
        "vector",
        "triangle",
        "circle",
        "cube",
        "rectangle",
        "trapezoid",
        "quadrant",
        "cellular automaton",
        "law of cosines",
        "trajectory",
        "path from",
        "directional arrows",
        "overlapping circles",
    )
    strict_diagram_terms = (
        "anatomical diagram",
        "network diagram",
        "queuing system",
        "math puzzle",
        "puzzle grid",
        "number circles",
        "operation buttons",
        "geometric diagram",
        "quadrant diagram",
        "schematic",
    )
    if any(term in haystack for term in diagram_terms) and (
        category in {"illustration", "other"}
        or any(term in haystack for term in strict_diagram_terms)
    ):
        return "diagram"

    if category not in {"illustration", "other"}:
        return category

    artistic_terms = (
        "editorial illustration",
        "illustration of",
        "illustrated",
        "artwork",
        "cartoon",
        "drawing",
        "engraving",
        "decorative",
    )
    if category == "illustration" or any(term in haystack for term in artistic_terms):
        return "artistic-illustration"
    return category


def _load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "ok" and row.get("identifier"):
                out.add(row["identifier"])
    return out


def _ensure_caption_header(path: Path) -> None:
    """Upgrade older caption CSVs before appending rows with new fields."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames == list(CAPTION_FIELDS):
            return
        rows = list(reader)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAPTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CAPTION_FIELDS})


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
            if not fp.exists():
                continue
            with fp.open("rb") as image_fh:
                if _is_image_body(image_fh.read(2048)):
                    targets.append((ident, fp))
    return targets


def caption_images(
    *,
    workers: int = 4,
    limit: int | None = None,
    only_screenshots: bool = True,
    model: str = DEFAULT_MODEL,
    force: bool = False,
    refs_path: Path = IMAGE_REFS_FILE,
    image_log_path: Path = IMAGE_LOG,
    out_path: Path = CAPTIONS_FILE,
) -> tuple[int, int]:
    """Classify a subset of downloaded images using LiteLLM vision.

    Returns ``(captioned, failed)``. Resumable via ``out_path``.
    """
    # Validate config before doing any target scan or log setup.
    headers = _litellm_headers()
    _litellm_url()

    ensure_dirs()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_caption_header(out_path)

    targets = _select_targets(
        refs_path, image_log_path, only_screenshots=only_screenshots
    )
    done = set() if force else _load_done(out_path)
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
    with (
        httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers=headers,
            verify=make_ssl_context(),
        ) as client,
        out_path.open("a", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=CAPTION_FIELDS)
        if write_header:
            writer.writeheader()
            fh.flush()

        def _process(item: tuple[str, Path]) -> int:
            ident, fp = item
            try:
                model_fp = _prepare_model_input(fp, identifier=ident)
                parsed = _classify_one(client, model_fp, model=model)
                ai_title = str(parsed.get("title", "")).strip()[:120]
                ai_description = str(parsed.get("description", "")).strip()[:300]
                ai_text = str(parsed.get("text", "")).strip()[:MAX_TEXT_CHARS]
                result = CaptionResult(
                    identifier=ident,
                    ai_category=infer_caption_category(
                        parsed.get("category", ""),
                        title=ai_title,
                        description=ai_description,
                        text=ai_text,
                    ),
                    ai_description=ai_description,
                    ai_title=ai_title,
                    ai_text=ai_text,
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
                        "ai_text": result.ai_text,
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
