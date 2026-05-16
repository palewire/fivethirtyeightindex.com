<script lang="ts">
	import { base } from '$app/paths';
</script>

<svelte:head>
	<title>About — fakethirtyeight.com</title>
</svelte:head>

<h1 class="section-heading">About</h1>

<p>
	<a href="https://fivethirtyeight.com" rel="noopener external" target="_blank"
		>fivethirtyeight.com</a
	>
	was an American data-journalism site founded by Nate Silver in 2008. It was taken offline by its
	corporate owners in 2025. This site is a community-built index of every article, liveblog, project,
	video, podcast, and methodology page that the
	<a href="https://web.archive.org/" rel="noopener external" target="_blank"
		>Internet Archive's Wayback Machine</a
	> captured during the site's lifetime.
</p>

<h2 class="section-heading">What's indexed</h2>

<p>
	Every URL was discovered by querying the Wayback Machine's CDX Server API for
	<code>*.fivethirtyeight.com</code> and walking the captured sitemaps. ~1.9 million unique URLs
	were collected, then filtered down to ~21,000 distinct editorial entries by classifying URLs by
	path pattern and rolling up sub-permalinks (liveblog updates, project drill-downs) into one row
	per logical entity.
</p>

<p>
	Headlines, bylines, and publication dates were extracted from each entry's newest 200-status
	Wayback snapshot using standard metadata signals — Open Graph tags, schema.org JSON-LD, Parse.ly
	tags, and template-specific DOM fallbacks. Coverage varies by era: modern <code>/features/</code>
	pages have near-100% fill; some pre-2010 Blogspot posts fall back to a year/month date derived from
	the URL path.
</p>

<h2 class="section-heading">What's not</h2>

<ul>
	<li>
		Articles are <strong>not</strong> republished here. Every headline links off to its Wayback Machine
		snapshot so the original capture, with its original markup and styling, remains the source of truth.
	</li>
	<li>
		Tag and category archive pages, paginated index views, author-archive pages, and login/auth
		callback URLs are filtered out — they aren't editorial content.
	</li>
	<li>
		The 2010–2013 NYT-era blog at <code>fivethirtyeight.blogs.nytimes.com</code> and the
		<code>538.com</code> short domain are out of scope.
	</li>
</ul>

<h2 class="section-heading">Source</h2>

<p>
	Code is on
	<a
		href="https://github.com/palewire/fakethirtyeight.com"
		rel="noopener external"
		target="_blank">GitHub</a
	>. Pull requests welcome.
</p>

<p>
	<a href="{base}/">← back to index</a>
</p>
