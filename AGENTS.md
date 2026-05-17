# AGENTS.md

Notes for AI agents working in this repo. Read this first.

## What this is

A two-part archive of FiveThirtyEight content: a Python data pipeline that scrapes the Wayback Machine, plus a SvelteKit static site that browses the result. Source of truth for what the pipeline does is [README.md](README.md); source of truth for how to set up your environment is [CONTRIBUTING.md](CONTRIBUTING.md). Read them.

## Repo orientation

- `src/fakethirtyeight/` — the pipeline. Entry point is `cli.py`. The pipeline stages are independent modules: `crawl.py`, `merge.py`, `classify.py`, `curate.py`, `enrich.py`, `feeds.py`, `sitemaps.py`, `site_data.py`, `duplicates.py`, `export.py`, `stats.py`. The classifier is the rule center — every URL pattern lives in `classify.py`.
- `web/` — SvelteKit, reads `web/static/data/articles.{json,csv}`. Frontend has no server side, no API; everything is static.
- `data/` — pipeline outputs. Mostly gitignored, but `data/enriched.csv` and `data/feed-*.csv` **are tracked** because they're the irreplaceable result of slow Wayback fetches.
- `tests/` — pytest. Mostly classification + curation + extractor unit tests. No network in tests.

## Hard rules

1. **Never paste credentials into chat.** The `.env` file is opaque — read with `cut -d= -f1 .env` if you need to confirm key *names* exist; never `cat` or `head` it. If you slip up, tell the user immediately and recommend they rotate the keys.
2. **Don't run `rm -rf data/` or rewrite `data/enriched.csv` carelessly.** Rebuilding the enriched file from scratch takes days of polite Wayback fetches. The tracked copy in git is the safety net.
3. **Pipeline files are big.** `data/index.csv` is 1.1 GB; `data/shards/` is 700 MB. Never `Read` them; use `awk` / `grep` / Python streaming.
4. **Don't commit secrets, `data/index.csv`, or `data/curated.csv`.** The `.gitignore` whitelist keeps you honest, but stage explicitly anyway.

## Common pitfalls (these have all bitten us)

- **`uv run python -c` doesn't auto-load `.env`** unless something in the package imports first. The dotenv hook lives in `src/fakethirtyeight/__init__.py`. If you need env vars in a one-liner, `import fakethirtyeight` first.
- **`rescrape-*` / `retry-failed` commands rewrite `data/enriched.csv` in place.** A previous version had a bug where `--limit N` broke out of the read loop early and truncated the file on write-back. Fixed in `_load_all_rows`, but stay alert when touching those functions — always read the whole file before processing targets.
- **Wayback CDX 403s on prefix queries against major news domains** (`*.nytimes.com`, `abcnews.go.com`). An IA S3 key alone doesn't fix it. The `feeds` command (walking archived Atom/RSS) is the working bypass for the NYT era — see `feeds.py` and the README.
- **Wayback is flaky under load.** When a background fetch is running, foreground curl/httpx requests will hit SSL EOF and 503s. Don't conclude the API is broken — wait, retry, or use the built-in `CdxClient` / `_fetch` helpers which already do tenacity-backed retries.
- **Brotli responses** from Wayback's `id_` endpoint show up as garbage bytes unless the `brotli` decoder is installed. It's already a project dep.
- **`load_enriched()` re-derives `rollup_key` from each row's URL on read.** Don't trust the on-disk `rollup_key` column as canonical — it reflects whatever classifier ran at write time. The runtime view always uses the current classifier.

## Working in a worktree

This repo is often used with multiple `.claude/worktrees/<name>/` clones in parallel. A few things to know:

- The `.git/` of a worktree is a pointer file, not a directory. Pre-commit hook lookups happen against the canonical `.git/`, not yours. Hooks installed in the main checkout *do* fire in worktrees.
- The intermediate `data/` files (`index.csv`, `curated.csv`, `shards/`) are gitignored and don't carry across worktrees. The committed `enriched.csv` + `feed-*.csv` do. If you need a full pipeline locally, either copy from a sibling worktree or rebuild via `crawl` → `merge` → `curate`.
- Edits made to file paths outside the worktree (e.g. directly under `/Users/.../fivethirtyeight.com/src/`) will not be visible to the worktree's working tree. Always edit under the worktree path.

## Verification before declaring done

For pipeline changes:
1. `uv run pytest tests/ --override-ini="addopts="` — full suite must stay green (171+ tests).
2. `uv run ty check src/fakethirtyeight` — type check.
3. If you changed `classify.py`: re-run `fakethirtyeight curate && fakethirtyeight build-site-data` and inspect `web/static/data/articles.csv` for the kind of entry you changed.
4. If you changed `metadata.py` or an extractor: write a unit test with a fixture HTML in `tests/`.

For frontend changes: start the dev server with `make site-dev` and visually verify. The agent-browser plugin can do this autonomously.

## Style and scope

- The user prefers terse responses with no trailing summaries. They read the diffs.
- Stay narrowly inside scope. If the user says "fix article dedup", don't also "improve" project dedup — flag it as a separate task and let them decide.
- When you find an out-of-scope issue worth fixing, mention it briefly and stop; don't expand the PR.
- One-off scripts go in `/tmp/`. Reusable logic goes in `src/fakethirtyeight/` with a CLI command and tests.
- Commit messages: the user's style is a concise first line stating *what + why*, then a short paragraph if the *why* isn't obvious. Match it.

## Long-running work

The `Bash` tool's `run_in_background` and `ScheduleWakeup` together handle long-running pipeline jobs well. Typical durations to plan around:

- `fakethirtyeight feeds` over a multi-year span: ~10–20 min
- `fakethirtyeight rescrape-dates` on the full corpus: ~30 min
- `fakethirtyeight enrich` from cold: many hours
- `fakethirtyeight crawl` from cold: ~30 min (CDX pages stream)

If you start one, schedule a wakeup *past the expected end* and continue with other work in the meantime.
