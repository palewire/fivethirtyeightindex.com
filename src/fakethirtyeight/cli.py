"""Click command group for the fakethirtyeight CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from fakethirtyeight import __version__
from fakethirtyeight import crawl as crawl_mod
from fakethirtyeight import curate as curate_mod
from fakethirtyeight import duplicates as duplicates_mod
from fakethirtyeight import enrich as enrich_mod
from fakethirtyeight import export as export_mod
from fakethirtyeight import merge as merge_mod
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
