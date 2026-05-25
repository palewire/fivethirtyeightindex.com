"""Click command group for the fakethirtyeight CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from fakethirtyeight import __version__
from fakethirtyeight import ai2html as ai2html_mod
from fakethirtyeight import ai2html_review as ai2html_review_mod
from fakethirtyeight import articles as articles_mod
from fakethirtyeight import caption as caption_mod
from fakethirtyeight import caption_review as caption_review_mod
from fakethirtyeight import crawl as crawl_mod
from fakethirtyeight import curate as curate_mod
from fakethirtyeight import datasets as datasets_mod
from fakethirtyeight import download_podcasts as download_podcasts_mod
from fakethirtyeight import duplicates as duplicates_mod
from fakethirtyeight import embeds as embeds_mod
from fakethirtyeight import enrich as enrich_mod
from fakethirtyeight import export as export_mod
from fakethirtyeight import feeds as feeds_mod
from fakethirtyeight import ia_html_upload as ia_html_upload_mod
from fakethirtyeight import ia_image_upload as ia_image_upload_mod
from fakethirtyeight import ia_upload as ia_upload_mod
from fakethirtyeight import images as images_mod
from fakethirtyeight import merge as merge_mod
from fakethirtyeight import podcast_metadata as podcast_metadata_mod
from fakethirtyeight import save as save_mod
from fakethirtyeight import save_now as save_now_mod
from fakethirtyeight import site_data as site_data_mod
from fakethirtyeight import sitemaps as sitemaps_mod
from fakethirtyeight import stats as stats_mod
from fakethirtyeight.paths import INDEX_FILE, STATE_FILE


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(__version__)
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Build an index of every fivethirtyeight.com URL captured by the Wayback Machine."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)


@cli.command()
@click.option("--host", default=crawl_mod.DEFAULT_HOST, show_default=True)
@click.option("--year", type=int, default=None, help="Limit to a single year shard.")
@click.option(
    "--start-year",
    type=int,
    default=crawl_mod.DEFAULT_START_YEAR,
    show_default=True,
    help="First year to shard.",
)
@click.option(
    "--end-year", type=int, default=None, help="Last year (default: current year)."
)
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Polite delay (seconds).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap rows per shard (smoke testing).",
)
@click.option(
    "--pages",
    type=int,
    default=None,
    help="Cap CDX pages per shard (smoke testing).",
)
def crawl(
    host: str,
    year: int | None,
    start_year: int,
    end_year: int | None,
    workers: int,
    delay: float,
    limit: int | None,
    pages: int | None,
) -> None:
    """Run/resume the sharded CDX crawl."""
    if year is not None:
        shards = [crawl_mod.Shard(host=host, year=year)]
    else:
        shards = crawl_mod.build_default_shards(
            host=host, start_year=start_year, end_year=end_year
        )
    click.echo(f"running {len(shards)} shard(s) with {workers} worker(s)")
    crawl_mod.run(
        shards,
        workers=workers,
        delay=delay,
        page_limit=pages,
        row_limit=limit,
    )


@cli.command()
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--delay", type=float, default=1.0, show_default=True)
@click.option("--host", default="fivethirtyeight.com", show_default=True)
def sitemaps(workers: int, delay: float, host: str) -> None:
    """Pull captured sitemap.xml files from Wayback, parse URLs."""
    count = sitemaps_mod.enrich(workers=workers, delay=delay, host=host)
    click.echo(f"discovered {count} URLs from sitemaps")


@cli.command()
@click.option("--feed-url", required=True, help="Archived feed URL to walk.")
@click.option(
    "--host",
    default=None,
    help="Host label for output files (defaults to feed URL hostname).",
)
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--delay", type=float, default=0.5, show_default=True)
@click.option(
    "--sample-every-days",
    type=int,
    default=None,
    help="Keep one memento per N-day bucket to reduce redundant fetches.",
)
@click.option("--start-year", type=int, default=None)
@click.option("--end-year", type=int, default=None)
def feeds(
    feed_url: str,
    host: str | None,
    workers: int,
    delay: float,
    sample_every_days: int | None,
    start_year: int | None,
    end_year: int | None,
) -> None:
    """Walk Wayback snapshots of an Atom/RSS feed to recover post URLs + metadata."""
    fetched, found = feeds_mod.walk(
        feed_url,
        host=host,
        workers=workers,
        delay=delay,
        sample_every_days=sample_every_days,
        start_year=start_year,
        end_year=end_year,
    )
    click.echo(
        f"fetched {fetched:,} feed mementos, discovered {found:,} unique post URLs"
    )


@cli.command("save-to-wayback")
@click.option(
    "--feed-csv",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a data/feed-*.csv whose 'url' column gets submitted.",
)
@click.option("--delay", type=float, default=5.0, show_default=True)
@click.option("--limit", type=int, default=None, help="Cap submissions (smoke test).")
def save_to_wayback(feed_csv: Path, delay: float, limit: int | None) -> None:
    """Submit each URL in a feed CSV to Wayback Save Page Now."""
    urls = list(save_mod.urls_from_feed_csv(feed_csv))
    if limit:
        urls = urls[:limit]
    click.echo(f"submitting {len(urls):,} URLs (delay={delay}s)")
    submitted, errored = save_mod.submit_urls(urls, delay=delay)
    click.echo(f"queued {submitted:,} for capture, {errored:,} failed")


@cli.command()
def merge() -> None:
    """Combine all shard CSVs into the deduplicated index."""
    count = merge_mod.merge()
    click.echo(f"wrote {count:,} unique URLs to {INDEX_FILE}")


@cli.command()
@click.option("--top", type=int, default=15, show_default=True)
def stats(top: int) -> None:
    """Print summary statistics over the merged index."""
    summary = stats_mod.summarize()
    click.echo(stats_mod.format_text(summary, top=top))


@cli.command("build-site-data")
def build_site_data() -> None:
    """Build web/static/data/articles.json from curated + enriched CSVs."""
    n = site_data_mod.build()
    click.echo(f"wrote {n:,} records to {site_data_mod.SITE_DATA_FILE}")


@cli.command("datasets")
@click.option(
    "--github-dates/--no-github-dates",
    default=False,
    help="Fetch first-commit dates from GitHub for dataset sources.",
)
def datasets(github_dates: bool) -> None:
    """Fetch FiveThirtyEight's dataset catalog and build site dataset files."""
    n = datasets_mod.scrape_index(include_commit_dates=github_dates)
    click.echo(
        f"wrote {n:,} datasets to {datasets_mod.DATASETS_FILE} "
        f"and {datasets_mod.SITE_DATASETS_FILE}"
    )


@cli.command("download-datasets")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many dataset bundles to download (smoke testing).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-download bundles already marked downloaded in the log.",
)
def download_datasets(limit: int | None, force: bool) -> None:
    """Download one local bundle of source files for each dataset."""
    downloaded, skipped, failed = datasets_mod.download_bundles(
        limit=limit,
        force=force,
    )
    click.echo(
        f"downloaded: {downloaded:,}\nskipped:    {skipped:,}\nfailed:     {failed:,}\n"
        f"\nfiles: {datasets_mod.DATASET_BUNDLES_DIR}\n"
        f"log:   {datasets_mod.DATASET_DOWNLOAD_LOG}"
    )


@cli.command("upload-datasets")
@click.option(
    "--collection",
    default=datasets_mod.DEFAULT_COLLECTION,
    show_default=True,
    help="archive.org collection slug to upload into.",
)
@click.option(
    "--contributor",
    default=datasets_mod.DEFAULT_CONTRIBUTOR,
    show_default=True,
    help="Person archiving these items (sets the `contributor` field).",
)
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Seconds to sleep between item uploads.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to upload (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-upload items even if the upload log already marks them uploaded.",
)
@click.option(
    "--path-sensitive-only",
    is_flag=True,
    help="Only upload bundles with nested files or duplicate basenames.",
)
def upload_datasets(
    collection: str,
    contributor: str,
    delay: float,
    limit: int | None,
    dry_run: bool,
    force: bool,
    path_sensitive_only: bool,
) -> None:
    """Upload each downloaded dataset bundle to archive.org as one item."""
    uploaded, skipped, failed = datasets_mod.upload_bundles(
        collection=collection,
        contributor=contributor,
        delay=delay,
        limit=limit,
        dry_run=dry_run,
        force=force,
        path_sensitive_only=path_sensitive_only,
    )
    click.echo(
        f"uploaded: {uploaded:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {datasets_mod.DATASET_UPLOAD_LOG}"
    )


@cli.command("repair-dataset-years")
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Seconds to sleep between metadata updates.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to repair (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Repair items even if the repair log already marks them done.",
)
def repair_dataset_years(
    delay: float,
    limit: int | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Backfill archive.org `year` metadata on uploaded dataset items."""
    repaired, skipped, failed = datasets_mod.repair_dataset_years(
        delay=delay,
        limit=limit,
        dry_run=dry_run,
        force=force,
    )
    click.echo(
        f"repaired: {repaired:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {datasets_mod.DATASET_METADATA_REPAIR_LOG}"
    )


@cli.command()
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Polite per-worker delay (seconds).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap total URLs to enrich (smoke testing).",
)
def enrich(workers: int, delay: float, limit: int | None) -> None:
    """Fetch each curated URL's snapshot and extract title/byline/published_at."""
    n = enrich_mod.enrich(workers=workers, delay=delay, limit=limit)
    click.echo(f"enriched {n:,} rows → {enrich_mod.ENRICHED_FILE}")


@cli.command("download-articles")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many to download (smoke testing).",
)
def download_articles(workers: int, limit: int | None) -> None:
    """Stream every curated article's Wayback snapshot HTML to disk.

    Reads data/enriched.csv, fetches each row's `wayback_url` (the raw
    id_ snapshot — no Wayback chrome), and writes gzipped HTML to
    data/articles/<year>/<hash>.html.gz. Resumable via
    data/article_download_log.csv.
    """
    ok, skipped, failed = articles_mod.download_articles(workers=workers, limit=limit)
    click.echo(
        f"downloaded:        {ok:,}\n"
        f"skipped (on disk): {skipped:,}\n"
        f"failed:            {failed:,}\n"
        f"\nfiles: {articles_mod.ARTICLES_DIR}\n"
        f"log:   {articles_mod.DOWNLOAD_LOG}"
    )


@cli.command("extract-images")
def extract_images() -> None:
    """Walk downloaded articles and write image references CSV.

    Reads data/articles/**/*.html.gz, parses each, emits one row per
    <img> / <picture><source> reference to data/image_references.csv.
    Drops ad pixels, tracking beacons, and theme UI chrome.
    """
    n = images_mod.extract_references()
    click.echo(f"wrote {n:,} image references to {images_mod.IMAGE_REFS_FILE}")


@cli.command("download-images")
@click.option("--workers", type=int, default=8, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many images to fetch (smoke testing).",
)
def download_images_cli(workers: int, limit: int | None) -> None:
    """Stream every referenced image to disk (live first, Wayback fallback).

    Reads data/image_references.csv (run `extract-images` first),
    deduplicates by canonical URL, and saves to
    data/images/<aa>/<sha1>.<ext>. Resumable via
    data/image_download_log.csv.
    """
    ok, skipped, failed = images_mod.download_images(workers=workers, limit=limit)
    click.echo(
        f"downloaded: {ok:,}\nskipped:    {skipped:,}\nfailed:     {failed:,}\n"
        f"\nfiles: {images_mod.IMAGES_DIR}\nlog:   {images_mod.IMAGE_LOG}"
    )


@cli.command("extract-ai2html")
def extract_ai2html() -> None:
    """Walk downloaded articles and write ai2html references CSV.

    Reads data/articles/**/*.html.gz, finds inline ai2html blocks and
    ABC-era pym ai2html embeds, and writes data/ai2html_references.csv.
    """
    n = ai2html_mod.extract_references()
    click.echo(f"wrote {n:,} ai2html references to {ai2html_mod.AI2HTML_REFS_FILE}")


@cli.command("download-ai2html")
@click.option("--workers", type=int, default=8, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many ai2html files to fetch (smoke testing).",
)
def download_ai2html_cli(workers: int, limit: int | None) -> None:
    """Save every referenced ai2html graphic as local HTML.

    Reads data/ai2html_references.csv (run `extract-ai2html` first),
    deduplicates by canonical URL, and saves to
    data/ai2html/<aa>/<sha1>.html. Resumable via
    data/ai2html_download_log.csv.
    """
    ok, skipped, failed = ai2html_mod.download_ai2html(workers=workers, limit=limit)
    click.echo(
        f"downloaded: {ok:,}\nskipped:    {skipped:,}\nfailed:     {failed:,}\n"
        f"\nfiles: {ai2html_mod.AI2HTML_DIR}\nlog:   {ai2html_mod.AI2HTML_LOG}"
    )


@cli.command("render-ai2html")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many ai2html screenshots to render (smoke testing).",
)
@click.option("--width", type=int, default=1000, show_default=True)
@click.option("--height", type=int, default=4096, show_default=True)
@click.option("--timeout", type=int, default=60, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Re-render already completed ai2html screenshots.",
)
def render_ai2html_cli(
    limit: int | None,
    width: int,
    height: int,
    timeout: int,
    force: bool,
) -> None:
    """Render downloaded ai2html graphics to desktop PNG screenshots.

    Reads data/ai2html_download_log.csv (run `download-ai2html` first)
    and saves PNG previews to data/ai2html_renders. Resumable via
    data/ai2html_render_log.csv.
    """
    ok, skipped, failed = ai2html_mod.render_ai2html(
        limit=limit,
        width=width,
        height=height,
        timeout=timeout,
        force=force,
    )
    click.echo(
        f"rendered: {ok:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nfiles: {ai2html_mod.AI2HTML_RENDER_DIR}\n"
        f"log:   {ai2html_mod.AI2HTML_RENDER_LOG}"
    )


@cli.command("review-ai2html-renders")
def review_ai2html_renders() -> None:
    """Build a local HTML report for reviewing ai2html render quality."""
    n = ai2html_review_mod.build_review()
    click.echo(f"wrote {n:,} rows to {ai2html_review_mod.REVIEW_FILE}")


@cli.command("extract-embeds")
def extract_embeds() -> None:
    """Walk downloaded articles and write non-ai2html embed references CSV.

    Reads data/articles/**/*.html.gz, finds project-style pym and iframe HTML
    embeds, excludes ai2html references, and writes data/embed_references.csv.
    """
    n = embeds_mod.extract_references()
    click.echo(f"wrote {n:,} embed references to {embeds_mod.EMBED_REFS_FILE}")


@cli.command("download-embeds")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many embed HTML files to fetch (smoke testing).",
)
def download_embeds_cli(workers: int, limit: int | None) -> None:
    """Save every referenced non-ai2html HTML embed as local HTML.

    Reads data/embed_references.csv (run `extract-embeds` first),
    deduplicates by canonical URL, and saves to data/embeds.
    Resumable via data/embed_download_log.csv.
    """
    ok, skipped, failed = embeds_mod.download_embeds(workers=workers, limit=limit)
    click.echo(
        f"downloaded: {ok:,}\nskipped:    {skipped:,}\nfailed:     {failed:,}\n"
        f"\nfiles: {embeds_mod.EMBED_DIR}\nlog:   {embeds_mod.EMBED_LOG}"
    )


@cli.command("render-embeds")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many embed screenshots to render (smoke testing).",
)
@click.option("--width", type=int, default=1000, show_default=True)
@click.option("--height", type=int, default=4096, show_default=True)
@click.option("--timeout", type=int, default=60, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Re-render already completed embed screenshots.",
)
def render_embeds_cli(
    limit: int | None,
    width: int,
    height: int,
    timeout: int,
    force: bool,
) -> None:
    """Render downloaded non-ai2html embeds to desktop PNG screenshots."""
    ok, skipped, failed = embeds_mod.render_embeds(
        limit=limit,
        width=width,
        height=height,
        timeout=timeout,
        force=force,
    )
    click.echo(
        f"rendered: {ok:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nfiles: {embeds_mod.EMBED_RENDER_DIR}\n"
        f"log:   {embeds_mod.EMBED_RENDER_LOG}"
    )


@cli.command("caption-images")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many to caption (smoke testing).",
)
@click.option(
    "--all/--screenshots-only",
    "do_all",
    default=False,
    show_default=True,
    help="Caption every downloaded image (instead of only screenshots).",
)
@click.option(
    "--model",
    default=caption_mod.DEFAULT_MODEL,
    show_default=True,
    help="LiteLLM model to use for classification.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Recaption already successful rows and append newer results.",
)
def caption_images(
    workers: int, limit: int | None, do_all: bool, model: str, force: bool
) -> None:
    """Use LiteLLM vision to classify ambiguous images.

    For each target image, asks the vision model to return a content category,
    description, and suggested title. Results land in
    data/image_captions.csv and are consumed by `upload-images` to
    decide which screenshots count as in-scope data viz.

    Requires LITELLM_API_KEY, LITELLM_BASE_URL, and LITELLM_USER_AGENT.
    """
    ok, failed = caption_mod.caption_images(
        workers=workers,
        limit=limit,
        only_screenshots=not do_all,
        model=model,
        force=force,
    )
    click.echo(
        f"captioned: {ok:,}\nfailed:    {failed:,}\n\nlog: {caption_mod.CAPTIONS_FILE}"
    )


@cli.command("review-image-captions")
def review_image_captions() -> None:
    """Build a local HTML report for reviewing image caption choices."""
    n = caption_review_mod.build_review()
    click.echo(f"wrote {n:,} rows to {caption_review_mod.REVIEW_FILE}")


@cli.command("upload-images")
@click.option(
    "--collection",
    default=ia_image_upload_mod.DEFAULT_COLLECTION,
    show_default=True,
    help="archive.org collection slug to upload into.",
)
@click.option(
    "--contributor",
    default=ia_image_upload_mod.DEFAULT_CONTRIBUTOR,
    show_default=True,
    help="Person archiving these items (sets the `contributor` field).",
)
@click.option(
    "--delay",
    type=float,
    default=0.5,
    show_default=True,
    help="Seconds to sleep between item uploads when --workers=1.",
)
@click.option(
    "--workers",
    type=int,
    default=1,
    show_default=True,
    help="Concurrent upload workers.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to upload (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
def upload_images(
    collection: str,
    contributor: str,
    delay: float,
    workers: int,
    limit: int | None,
    dry_run: bool,
) -> None:
    """Upload each downloaded article image to archive.org as a standalone item.

    Reads data/image_download_log.csv (run `download-images` first),
    joins data/image_references.csv + data/enriched.csv for metadata
    (alt/caption/article title/byline/date), and uploads one IA item
    per image. Resumable via data/image_upload_log.csv.

    Items default to the curated FiveThirtyEight collection. Requires
    IA_ACCESS_KEY + IA_SECRET_KEY.
    """
    uploaded, skipped, failed = ia_image_upload_mod.upload_images(
        collection=collection,
        contributor=contributor,
        delay=delay,
        workers=workers,
        limit=limit,
        dry_run=dry_run,
    )
    click.echo(
        f"uploaded: {uploaded:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {ia_image_upload_mod.UPLOAD_LOG}"
    )


@cli.command("upload-html-graphics")
@click.option(
    "--collection",
    default=ia_html_upload_mod.DEFAULT_COLLECTION,
    show_default=True,
    help="archive.org collection slug to upload into.",
)
@click.option(
    "--contributor",
    default=ia_html_upload_mod.DEFAULT_CONTRIBUTOR,
    show_default=True,
    help="Person archiving these items (sets the `contributor` field).",
)
@click.option(
    "--delay",
    type=float,
    default=0.5,
    show_default=True,
    help="Seconds to sleep between item uploads.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to upload (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
def upload_html_graphics(
    collection: str,
    contributor: str,
    delay: float,
    limit: int | None,
    dry_run: bool,
) -> None:
    """Upload rendered ai2html/embed bundles to archive.org.

    Uploads the rendered PNG first so it acts as the item thumbnail/lead
    image, then uploads the extracted HTML source as the preserved bundle.
    Resumable via data/html_graphic_upload_log.csv.

    Requires IA_ACCESS_KEY + IA_SECRET_KEY unless --dry-run is used.
    """
    uploaded, skipped, failed = ia_html_upload_mod.upload_html_graphics(
        collection=collection,
        contributor=contributor,
        delay=delay,
        limit=limit,
        dry_run=dry_run,
    )
    click.echo(
        f"uploaded: {uploaded:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {ia_html_upload_mod.UPLOAD_LOG}"
    )


@cli.command("download-podcasts")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many to download (smoke testing).",
)
def download_podcasts(workers: int, limit: int | None) -> None:
    """Stream every podcast MP3 to data/podcasts/ for later upload.

    Resumable via data/podcast_download_log.csv. Existing files are
    skipped so re-running only fetches what's missing or previously
    failed.
    """
    ok, failed = download_podcasts_mod.download_podcasts(workers=workers, limit=limit)
    click.echo(
        f"downloaded: {ok:,}\nfailed:     {failed:,}\n"
        f"\nfiles: {download_podcasts_mod.PODCASTS_DIR}\n"
        f"log:   {download_podcasts_mod.DOWNLOAD_LOG}"
    )


@cli.command("save-podcasts")
@click.option(
    "--delay",
    type=float,
    default=3.0,
    show_default=True,
    help="Seconds to sleep between submissions.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many URLs to submit (smoke testing).",
)
@click.option(
    "--no-skip-recent",
    is_flag=True,
    help="Submit even if archive.org already has a capture.",
)
def save_podcasts(delay: float, limit: int | None, no_skip_recent: bool) -> None:
    """Submit podcast MP3 URLs to archive.org Save Page Now.

    Requires IA_ACCESS_KEY + IA_SECRET_KEY env vars
    (https://archive.org/account/s3.php). Resumable via
    data/podcast_archive_log.csv.
    """
    submitted, skipped, failed = save_now_mod.archive_podcast_mp3s(
        delay=delay, limit=limit, skip_recent=not no_skip_recent
    )
    click.echo(
        f"submitted: {submitted:,}\nskipped:   {skipped:,}\nfailed:    {failed:,}\n"
        f"\nlog: {save_now_mod.PODCAST_LOG}"
    )


@cli.command("podcast-metadata")
@click.option(
    "--id3/--no-id3",
    default=True,
    show_default=True,
    help="Run Tier 2 (ID3 tag extraction from downloaded MP3s) after Tier 1.",
)
def podcast_metadata(id3: bool) -> None:
    """Build data/podcast_metadata.csv for archive.org item uploads.

    Tier 1 (always): URL-derived fields — show, slug, date (when in the
    filename), megaphone ID, archive.org identifier, embedding player URL.

    Tier 2 (default on): patch in title/description/date/cover-art from
    the ID3 tags of each downloaded MP3 in data/podcasts/.
    """
    n = podcast_metadata_mod.build_tier1()
    click.echo(f"Tier 1: wrote {n:,} rows to {podcast_metadata_mod.METADATA_FILE}")
    if id3:
        enriched = podcast_metadata_mod.enrich_with_id3()
        click.echo(f"Tier 2: enriched {enriched:,} rows with ID3 tags")


@cli.command("upload-podcasts")
@click.option(
    "--collection",
    default=ia_upload_mod.DEFAULT_COLLECTION,
    show_default=True,
    help="archive.org collection slug to upload into.",
)
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Seconds to sleep between item uploads.",
)
@click.option(
    "--contributor",
    default=ia_upload_mod.DEFAULT_CONTRIBUTOR,
    show_default=True,
    help="Person archiving these items (sets the `contributor` field).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to upload (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
def upload_podcasts(
    collection: str,
    contributor: str,
    delay: float,
    limit: int | None,
    dry_run: bool,
) -> None:
    """Upload each podcast MP3 + cover art to archive.org as a collection item.

    Reads data/podcast_metadata.csv (run `podcast-metadata` first) and
    uploads one IA item per row. Resumable via data/podcast_upload_log.csv.

    Requires IA_ACCESS_KEY + IA_SECRET_KEY and the target collection
    to be granted to the account.
    """
    uploaded, skipped, failed = ia_upload_mod.upload_podcasts(
        collection=collection,
        contributor=contributor,
        delay=delay,
        limit=limit,
        dry_run=dry_run,
    )
    click.echo(
        f"uploaded: {uploaded:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {ia_upload_mod.UPLOAD_LOG}"
    )


@cli.command("repair-podcast-years")
@click.option(
    "--delay",
    type=float,
    default=1.0,
    show_default=True,
    help="Seconds to sleep between metadata updates.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap how many items to repair (smoke testing).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Log what would happen without hitting archive.org.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Repair items even if the repair log already marks them done.",
)
def repair_podcast_years(
    delay: float,
    limit: int | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Backfill archive.org `year` metadata on uploaded podcast items."""
    repaired, skipped, failed = ia_upload_mod.repair_podcast_years(
        delay=delay,
        limit=limit,
        dry_run=dry_run,
        force=force,
    )
    click.echo(
        f"repaired: {repaired:,}\nskipped:  {skipped:,}\nfailed:   {failed:,}\n"
        f"\nlog: {ia_upload_mod.METADATA_REPAIR_LOG}"
    )


@cli.command("rescrape-bylines")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--delay", type=float, default=1.0, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap rows to re-fetch (smoke testing).",
)
def rescrape_bylines(workers: int, delay: float, limit: int | None) -> None:
    """Re-fetch rows with empty byline and try the updated extractor."""
    total, recovered, errored = enrich_mod.rescrape_bylines(
        workers=workers, delay=delay, limit=limit
    )
    click.echo(
        f"rescraped {total:,} rows, recovered {recovered:,} byline(s), "
        f"{errored:,} transient failure(s)"
    )


@cli.command("rescrape-dates")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--delay", type=float, default=1.0, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap rows to re-fetch (smoke testing).",
)
def rescrape_dates(workers: int, delay: float, limit: int | None) -> None:
    """Upgrade YYYY-MM rows to full YYYY-MM-DD via the Blogspot date-header."""
    total, recovered, errored = enrich_mod.rescrape_dates(
        workers=workers, delay=delay, limit=limit
    )
    click.echo(
        f"rescraped {total:,} rows, recovered {recovered:,} full date(s), "
        f"{errored:,} transient failure(s)"
    )


@cli.command("retry-failed")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--delay", type=float, default=1.0, show_default=True)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap rows to re-fetch (smoke testing).",
)
def retry_failed(workers: int, delay: float, limit: int | None) -> None:
    """Re-fetch rows that errored or came back with no metadata at all."""
    total, recovered, errored = enrich_mod.retry_failed(
        workers=workers, delay=delay, limit=limit
    )
    click.echo(
        f"retried {total:,} rows, recovered {recovered:,}, "
        f"{errored:,} transient failure(s)"
    )


@cli.command()
def curate() -> None:
    """Filter to editorial URLs + roll up liveblogs/projects → data/curated.csv."""
    summary = curate_mod.curate()
    click.echo(
        f"input rows:        {summary.total_in:>10,}\n"
        f"qualifying (200 HTML, editorial kind): {summary.total_kept:>10,}\n"
        f"rollup groups:     {summary.out_rows:>10,}\n"
    )
    for kind, n in sorted(summary.by_kind.items(), key=lambda kv: -kv[1]):
        click.echo(f"  {kind:<14s} {n:>10,}")
    click.echo(f"\nwrote {curate_mod.CURATED_FILE}")


@cli.command()
@click.option(
    "--sample-size",
    type=int,
    default=5,
    show_default=True,
    help="Number of example URLs to include in each duplicate group row.",
)
def duplicates(sample_size: int) -> None:
    """Build duplicate-URL reports (by content digest + by canonical key)."""
    summary = duplicates_mod.report(sample_size=sample_size)
    click.echo(
        f"digest groups (same content at >1 URL):    {summary.digest_groups:>10,} "
        f"({summary.digest_dupe_urls:,} URLs)"
    )
    click.echo(
        f"canonical groups (same canonical key):     {summary.canonical_groups:>10,} "
        f"({summary.canonical_dupe_urls:,} URLs)"
    )
    click.echo(f"wrote {duplicates_mod.DIGEST_REPORT}")
    click.echo(f"wrote {duplicates_mod.CANONICAL_REPORT}")


@cli.command()
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["jsonl", "parquet"]),
    required=True,
)
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
def export(fmt: str, out_path: Path) -> None:
    """Convert the merged index to JSONL or Parquet."""
    if fmt == "jsonl":
        count = export_mod.to_jsonl(out_path)
    else:
        count = export_mod.to_parquet(out_path)
    click.echo(f"wrote {count:,} rows to {out_path}")


@cli.group()
def state() -> None:
    """Inspect/manage resume state."""


@state.command("show")
def state_show() -> None:
    """Print the current state file."""
    if not STATE_FILE.exists():
        click.echo(f"no state file at {STATE_FILE}")
        return
    click.echo(STATE_FILE.read_text())


@state.command("reset")
@click.confirmation_option(prompt="Really delete the state file?")
def state_reset() -> None:
    """Delete the state file (forces all shards to restart)."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        click.echo(f"removed {STATE_FILE}")
    else:
        click.echo("no state file to remove")
