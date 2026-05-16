<script lang="ts">
	import { base } from '$app/paths';
</script>

<footer>
	Created by <a href="https://palewi.re/">Ben Welsh</a>.
	<a href="{base}/data/articles.csv">Download the full index as CSV</a>. Open source on
	<a
		href="https://github.com/palewire/fivethirtyeightindex.com"
		rel="noopener external"
		target="_blank">GitHub</a
	>.
</footer>

<style>
	footer {
		margin-top: var(--space-xl);
		padding-top: var(--space-sm);
		border-top: var(--rule-thick);
		color: var(--color-muted);
		font-size: var(--font-size-small);
		font-weight: var(--font-weight-light);
	}

	/* Inline footer prose — the comma-separated links blend into the
	   surrounding gray text without an underline to call them out. */
	footer a {
		color: var(--color-muted);
		text-decoration: underline;
	}

	footer a:hover {
		color: var(--color-fg);
	}
</style>
