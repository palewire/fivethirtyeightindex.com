# fakethirtyeight

The old [fivethirtyeight.com](https://fivethirtyeight.com/) was taken offline by its corporate owners. This repo spiders the [Wayback Machine](https://web.archive.org/) (and a few adjacent sources) to build a comprehensive, deduplicated index of every editorial entry FiveThirtyEight ever published, then serves a browse + search UI at [fivethirtyeightindex.com](https://fivethirtyeightindex.com).

## What's in the box

| layer | path | purpose |
|---|---|---|
| Data pipeline | `src/fakethirtyeight/`, `fakethirtyeight` CLI | Crawl Wayback → classify → curate → enrich → emit static site data |
| Frontend | `web/` (SvelteKit) | Reads `web/static/data/articles.{json,csv}` and renders the archive |
| Tracked data | `data/enriched.csv`, `data/feed-*.csv` | The slow-to-regenerate Wayback enrichment + feed harvest |
| Published data | `web/static/data/` | What the site actually serves |

## How the pipeline works

```
  crawl  ──┐
           ├──►  merge ──► curate ──► enrich ──► build-site-data
  feeds  ──┤                          ▲
           │                          │
  sitemaps ┘                          rescrape-bylines / rescrape-dates / retry-failed
```

1. **`crawl`** queries the Wayback [CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server-webapp) under `fivethirtyeight.com` (with `matchType=domain`, so every subdomain is captured). Sharded by year, resumable from `data/state.json`. Output: `data/shards/cdx-<year>-<host>.csv`.
2. **`sitemaps`** finds captured sitemap.xml URLs in the index, fetches the latest snapshot of each, parses recursively, and writes a `data/shards/sitemap-<host>.csv`.
3. **`feeds`** walks Wayback's timemap of an Atom/RSS feed URL, parses every captured snapshot, and emits both a sitemap-style shard and a pre-enriched metadata file (`data/feed-<host>.csv`). This is how the NYT-era content was recovered when CDX domain queries were 403'd — see [Working around CDX restrictions](#working-around-cdx-restrictions).
4. **`merge`** SURT-dedups every shard into `data/index.csv`. The classifier runs here, assigning each URL an editorial `kind` (`article`, `liveblog`, `project`, `video`, `podcast`, `methodology`, etc.) and a `rollup_key` so duplicates across URL variants collapse downstream.
5. **`curate`** filters the index to editorial 200-HTML URLs and rolls up by `rollup_key` into `data/curated.csv` (one row per logical entry, picking the best representative URL).
6. **`enrich`** fetches each curated URL through Wayback's raw `id_` endpoint and extracts title / byline / publish date with `metadata.py`. Output: `data/enriched.csv`. Three repair commands live alongside it:
   - **`retry-failed`** re-fetches rows that errored or came back without metadata.
   - **`rescrape-bylines`** re-fetches rows with a missing byline.
   - **`rescrape-dates`** re-fetches rows with only `YYYY-MM` precision and pulls a full date from the Blogspot-era `date-header` markup.
7. **`build-site-data`** joins curated + enriched, dedups article slug variants (`_N` revision suffixes, truncated slugs, typo-fixes that left both alive), and writes `web/static/data/articles.{json,csv}` plus `articles-meta.json` and `sitemap.xml`.

## Install

```bash
make install        # uv sync --all-extras
make site-install   # npm install in web/
```

### Working around CDX restrictions

The public Wayback CDX endpoint rejects prefix/domain queries against high-traffic news domains (e.g. `*.nytimes.com`, `abcnews.go.com`) with `403 Forbidden`. Two paths around it:

1. **Authenticate** — generate an Internet Archive S3 key pair at <https://archive.org/account/s3.php> and put them in a project-local `.env`:
   ```
   IA_ACCESS_KEY=...
   IA_SECRET_KEY=...
   ```
   The CLI auto-loads `.env` via `python-dotenv` and sets the auth header on every Wayback request. (Note: as of this writing, S3 keys alone aren't enough — the CDX server has a separate allowlist for high-traffic domains. Email <iathelp@archive.org> if you need it.)
2. **Walk feeds instead** — `fakethirtyeight feeds --feed-url <archived-rss-or-atom-url>` enumerates posts from captured feed snapshots and writes a sitemap-style shard. This bypasses CDX entirely and is how the 2010–2014 NYT era and 2017+ podcast catalog get into the index.

## Usage

```bash
# Sharded parallel crawl across every year, 4 workers, 1s polite delay per request.
fakethirtyeight crawl

# Or limit to a single year shard:
fakethirtyeight crawl --year 2014 --workers 1
fakethirtyeight crawl --host fivethirtyeight.blogs.nytimes.com   # CDX auth required

# Deduplicate every shard CSV into data/index.csv:
fakethirtyeight merge

# Pull captured sitemap.xml files and fold their URLs into the next merge:
fakethirtyeight sitemaps
fakethirtyeight merge

# Walk archived Atom/RSS feeds (also feeds the merge):
fakethirtyeight feeds \
  --feed-url https://fivethirtyeight.blogs.nytimes.com/feed/ \
  --sample-every-days 5 --start-year 2010 --end-year 2014
fakethirtyeight feeds --feed-url http://feeds.feedburner.com/538dotcom --sample-every-days 5
fakethirtyeight feeds --feed-url https://feeds.megaphone.fm/ESP8794877317   # podcast

# Curate + enrich + repair passes:
fakethirtyeight curate
fakethirtyeight enrich --workers 4 --delay 1.0
fakethirtyeight retry-failed
fakethirtyeight rescrape-dates
fakethirtyeight rescrape-bylines

# Duplicate reports (content-hash + canonical-URL dedup audits):
fakethirtyeight duplicates

# Summary stats:
fakethirtyeight stats

# Resume state inspection:
fakethirtyeight state show
fakethirtyeight state reset

# Convert the index to JSONL or Parquet:
fakethirtyeight export --format jsonl --out data/index.jsonl
fakethirtyeight export --format parquet --out data/index.parquet   # needs notebooks extras

# Build the site data files:
fakethirtyeight build-site-data
```

### Smoke-testing options

`crawl` accepts `--limit N` (cap rows per shard) and `--pages N` (cap CDX pages per shard) for quick end-to-end runs. The `retry-*` / `rescrape-*` commands accept `--limit N` for the same reason.

## Output schema

`data/index.csv` has one row per unique URL:

| column                  | description                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| `urlkey`                | SURT-normalized key (from CDX; computed locally for sitemap-only URLs)                     |
| `url`                   | original URL                                                                               |
| `canonical_key`         | a stricter normalization used for cross-host dedup audits                                  |
| `kind`                  | editorial kind from the classifier (`article`, `liveblog`, `project`, …)                   |
| `rollup_key`            | shared key for logically-equivalent URLs (`article:<slug>` etc.)                           |
| `host` / `path` / `query` | parsed from `url`                                                                        |
| `first_seen_ts`         | earliest CDX timestamp observed (`YYYYMMDDHHMMSS`)                                         |
| `last_seen_ts`          | latest CDX timestamp observed                                                              |
| `latest_status`         | HTTP status of the latest CDX observation                                                  |
| `latest_mimetype`       | mimetype of the latest CDX observation                                                     |
| `latest_digest`         | content hash of the latest CDX observation                                                 |
| `latest_length`         | byte length of the latest CDX observation                                                  |
| `snapshot_observations` | number of CDX rows we saw for this URL across all shards                                   |
| `source`                | `cdx`, `sitemap`, or `cdx+sitemap`                                                         |

`data/enriched.csv` adds the fetched per-URL metadata (title / byline / `published_at` / `extracted_via` / http status). It's keyed by `rollup_key` on disk, but the loader re-derives the key from each row's URL using the current classifier — so changes to classification rules don't strand existing enrichment.

## Resumability

Every CDX page boundary persists progress to `data/state.json`. Interrupt with `Ctrl-C` and re-run `fakethirtyeight crawl` — each shard picks up from its last resume key. Already-completed shards are skipped. `enrich` is similarly resumable: rows already in `enriched.csv` are skipped on subsequent runs.

## Frontend (SvelteKit static site)

A read-only browse + search UI lives in [`web/`](./web/). It loads `web/static/data/articles.json` (generated by `build-site-data`) and renders a Hacker News–style index with date/byline navigation and client-side search via MiniSearch.

```bash
make site-install        # one-time: npm install in web/
make site-data           # rebuild articles.json from data/enriched.csv + data/curated.csv
make site-dev            # run the SvelteKit dev server (http://localhost:5173)
make site-build          # produce the static build in web/build/
```

The site deploys to GitHub Pages on push to `main` via [.github/workflows/site.yaml](.github/workflows/site.yaml). Base path is auto-detected from `web/svelte.config.js` — empty when a `CNAME` is present in `web/static/`, otherwise `/fakethirtyeight.com` for the gh-pages subpath.

## Develop

```bash
make test         # pytest with coverage (no network)
make lint
make format
make type-check   # ty
```

CI runs ruff, ty, and pytest across Python 3.11–3.14 on every push. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full developer workflow.

## License

MIT
