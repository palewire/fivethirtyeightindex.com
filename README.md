# fakethirtyeight

The old [fivethirtyeight.com](https://fivethirtyeight.com/) was taken offline by its corporate owners. This tool spiders the [Wayback Machine](https://web.archive.org/) to build a comprehensive, deduplicated index of every unique URL ever captured under any `*.fivethirtyeight.com` host.

The output is a single portable CSV (`data/index.csv`) intended as the foundation for downstream archival, analysis, and content-rehydration projects.

## How it works

1. **CDX crawl.** Queries the Wayback Machine [CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server-webapp) for every URL captured under `fivethirtyeight.com` (with `matchType=domain`, which catches every subdomain — `www.`, `projects.`, `data.`, `blog.`, etc.) The query is sharded by year for parallelism and resumability; each shard writes an append-only CSV under `data/shards/`.
2. **Sitemap enrichment.** After merging, finds any sitemap.xml URLs in the index, fetches the newest captured snapshot of each via Wayback's `id_` raw-content endpoint, parses the XML, and folds any new URLs back in. Sitemap-index files are followed recursively.
3. **Merge.** Deduplicates every shard CSV by SURT urlkey into the final `data/index.csv` with first/last-seen aggregates and a `source` column tagging whether the URL came from CDX, a sitemap, or both.

State for resumable shard crawls lives in `data/state.json`.

## Install

```bash
make install
```

(Uses [uv](https://docs.astral.sh/uv/). Equivalent: `uv sync --all-extras`.)

## Usage

```bash
# Sharded parallel crawl across every year, 4 workers, 1s polite delay per request.
fakethirtyeight crawl

# Or limit to a single year shard:
fakethirtyeight crawl --year 2014 --workers 1

# Deduplicate every shard CSV into data/index.csv:
fakethirtyeight merge

# Pull captured sitemap.xml files and fold their URLs into the next merge:
fakethirtyeight sitemaps
fakethirtyeight merge

# Summary stats:
fakethirtyeight stats

# Resume state inspection:
fakethirtyeight state show
fakethirtyeight state reset    # nukes data/state.json

# Convert the index to JSONL or Parquet:
fakethirtyeight export --format jsonl --out data/index.jsonl
fakethirtyeight export --format parquet --out data/index.parquet   # needs pyarrow extras
```

### Smoke-testing options

`crawl` accepts `--limit N` (cap rows per shard) and `--pages N` (cap CDX pages per shard) for quick end-to-end runs.

## Output schema

`data/index.csv` has one row per unique URL:

| column                  | description                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| `urlkey`                | SURT-normalized key (from CDX; computed locally for sitemap-only URLs)                     |
| `url`                   | original URL                                                                               |
| `host` / `path` / `query` | parsed from `url`                                                                        |
| `first_seen_ts`         | earliest CDX timestamp observed for this URL across all shards (`YYYYMMDDHHMMSS`)          |
| `last_seen_ts`          | latest CDX timestamp observed                                                              |
| `latest_status`         | HTTP status of the latest CDX observation                                                  |
| `latest_mimetype`       | mimetype of the latest CDX observation                                                     |
| `latest_digest`         | content hash of the latest CDX observation (useful for dedup against an HTML cache)        |
| `latest_length`         | byte length of the latest CDX observation                                                  |
| `snapshot_observations` | number of CDX rows we saw for this URL across all shards (not total Wayback captures)      |
| `source`                | `cdx`, `sitemap`, or `cdx+sitemap`                                                         |

## Resumability

Every CDX page boundary persists progress to `data/state.json`. Interrupt with `Ctrl-C` and re-run `fakethirtyeight crawl` — each shard picks up from its last resume key. Already-completed shards are skipped.

## Frontend (SvelteKit static site)

A read-only browse + search UI lives in [`web/`](./web/). It loads
`web/static/data/articles.json` (generated from the enriched CSVs) and
renders a Hacker News–style index with date/byline navigation and
client-side search.

```bash
make site-install        # one-time: npm install in web/
make site-data           # build articles.json from data/enriched.csv + data/curated.csv
make site-dev            # run the SvelteKit dev server
make site-build          # produce the static build in web/build/
```

The site deploys to GitHub Pages on every push to `main` via
[.github/workflows/site.yaml](.github/workflows/site.yaml). It expects
to live at `https://palewire.github.io/fakethirtyeight.com/` — base path
configured in [`web/svelte.config.js`](web/svelte.config.js).

## Develop

```bash
make test         # pytest with coverage (no network)
make lint
make format
make type-check
```

## License

MIT
