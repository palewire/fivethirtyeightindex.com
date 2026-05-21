# Contributing

The repository is split into two pieces that share a tree:

- **`src/fakethirtyeight/`** — a Python package + `fakethirtyeight` CLI that crawls the Wayback Machine, classifies and dedupes URLs, fetches metadata, and emits the static-site data files.
- **`web/`** — a SvelteKit static site that consumes those files and renders the archive at [fivethirtyeightindex.com](https://fivethirtyeightindex.com).

## Setup

Install the Python toolchain (uses [uv](https://docs.astral.sh/uv/)) and the Node deps:

```sh
make install
make site-install
```

Install pre-commit hooks so the linters run on every commit:

```sh
uv run pre-commit install
```

If you want to rebuild the data pipeline from scratch, generate Internet Archive S3-style keys at <https://archive.org/account/s3.php> and add them to a project-local `.env`:

```
IA_ACCESS_KEY=...
IA_SECRET_KEY=...
```

The CLI auto-loads `.env` on import. The keys are only required for CDX prefix queries against rate-limited hosts (e.g. `*.nytimes.com`).

## Common tasks

```sh
# Python checks
make test          # pytest with coverage
make lint          # ruff check
make format        # ruff format
make type-check    # ty
make fix           # auto-fix lint issues where possible

# Data pipeline
fakethirtyeight crawl                # CDX shards under data/shards/
fakethirtyeight merge                # → data/index.csv
fakethirtyeight curate               # → data/curated.csv
fakethirtyeight enrich               # fetch + extract title/byline/date
fakethirtyeight feeds --feed-url ... # walk archived RSS/Atom feeds
fakethirtyeight build-site-data      # → web/static/data/articles.{json,csv}

# Site
make site-dev      # SvelteKit dev server on localhost:5173
make site-build    # production build to web/build/
```

Run `fakethirtyeight --help` for the full command list.

## What lives in `data/` vs. `web/static/data/`

- **`data/`** holds the raw + intermediate pipeline outputs. Most of it is gitignored; only `enriched.csv` and `feed-*.csv` are tracked because they represent thousands of polite Wayback fetches that aren't cheap to redo. The CDX shards and the merged index are recoverable from `fakethirtyeight crawl` + `fakethirtyeight merge`.
- **`web/static/data/`** holds the published artifacts the frontend reads (`articles.json`, `articles.csv`, `articles-meta.json`, `sitemap.xml`). These are committed because they're the input to the build.

## Pull requests

- Keep the Python changes and the rebuilt `web/static/data/` files in the same PR when they're causally linked — the data files are part of the change.
- The CI runs ruff, ty, and pytest across Python 3.11–3.14 on every push.
- The site preview deploys automatically on PR via the `site.yaml` workflow.
