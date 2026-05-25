# FiveThirtyEight Thumbnail Cache Worker

This Worker serves `/:identifier` from a dedicated thumbnail subdomain and proxies
Archive.org item thumbnails from `https://archive.org/services/img/:identifier`.

The static site writes graphic and illustration thumbnail URLs as:

```text
https://thumbs.fivethirtyeightindex.com/fivethirtyeight-image-example
```

Deploy with:

```sh
cd workers/thumb-cache
npx wrangler deploy
```

The Worker custom domain is `thumbs.fivethirtyeightindex.com`. It caches successful
image responses at the Cloudflare edge for one year and rejects non-image origin
responses so Archive.org error pages do not become thumbnails.
