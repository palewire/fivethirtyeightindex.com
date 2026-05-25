<script lang="ts">
	import { PaginatedGraphicGrid, SearchBox } from '$lib/components';
	import { monthLabel } from '$lib/data';
	import { base } from '$app/paths';
	import type { Graphic } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	function matches(illustration: Graphic, q: string): boolean {
		const haystack = [
			illustration.title,
			illustration.description,
			illustration.text,
			illustration.article_title,
			illustration.byline,
			illustration.source_url,
			illustration.id
		]
			.join(' ')
			.toLowerCase();
		return haystack.includes(q);
	}

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.illustrations;
		return data.illustrations.filter((illustration) => matches(illustration, q));
	});
</script>

<svelte:head>
	<title>{data.year} illustrations — fivethirtyeightindex.com</title>
</svelte:head>

<p class="eyebrow"><a href="{base}/illustrations/">All illustrations</a></p>
<h1 class="section-heading">{data.year} illustrations</h1>

{#if data.months.length > 1}
	<nav class="month-nav" aria-label="Jump to a month">
		{#each data.months as ym (ym)}
			<a href="{base}/illustrations/year/{data.year}/{ym.slice(5)}/">{monthLabel(ym.slice(5))}</a>
		{/each}
	</nav>
{/if}

<SearchBox bind:value={query} placeholder="Search by title or byline…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<div class="illustrations-summary">
		{filtered.length.toLocaleString()} {filtered.length === 1 ? 'match' : 'matches'}
	</div>
	<PaginatedGraphicGrid
		graphics={filtered}
		label={`${data.year} illustrations`}
		showCategory={false}
		showItemLabel={false}
		imageFit="cover"
		fallbackLabel="Illustration"
		showFallbackThumbLabel={false}
	/>
{/if}

<style>
	.eyebrow {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
	}

	.month-nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.75rem;
		margin: 0 0 0.75rem;
		font-size: 0.9rem;
	}

	.illustrations-summary {
		margin: 0.2rem 0 0.75rem;
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}
</style>
