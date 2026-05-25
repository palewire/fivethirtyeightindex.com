<script lang="ts">
	import { PaginatedGraphicGrid, SearchBox, YearList } from '$lib/components';
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
	<title>Illustrations — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.total.toLocaleString()} illustrations</h1>

<YearList years={data.years} hrefPrefix="/illustrations/year" showHeading={false} />

<SearchBox bind:value={query} placeholder="Search by title or byline…" />

{#if filtered.length === 0}
	<p class="no-results">
		{data.total === 0 ? 'No uploaded illustrations yet.' : 'No matches.'}
	</p>
{:else}
	<div class="illustrations-summary">
		{filtered.length.toLocaleString()} {filtered.length === 1 ? 'match' : 'matches'}
	</div>
	<PaginatedGraphicGrid
		graphics={filtered}
		label="Illustrations"
		showCategory={false}
		showItemLabel={false}
		imageFit="cover"
		fallbackLabel="Illustration"
		showFallbackThumbLabel={false}
	/>
{/if}

<style>
	.illustrations-summary {
		margin: 0.2rem 0 0.75rem;
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}

</style>
