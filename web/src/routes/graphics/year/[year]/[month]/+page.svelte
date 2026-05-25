<script lang="ts">
	import { PaginatedGraphicGrid, SearchBox } from '$lib/components';
	import { base } from '$app/paths';
	import type { Graphic } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	function matches(graphic: Graphic, q: string): boolean {
		const haystack = [
			graphic.title,
			graphic.category,
			graphic.description,
			graphic.text,
			graphic.article_title,
			graphic.byline,
			graphic.source_url,
			graphic.id
		]
			.join(' ')
			.toLowerCase();
		return haystack.includes(q);
	}

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.graphics;
		return data.graphics.filter((graphic) => matches(graphic, q));
	});
</script>

<svelte:head>
	<title>{data.monthName} {data.year} graphics — fivethirtyeightindex.com</title>
</svelte:head>

<p class="breadcrumb">
	<a href="{base}/graphics/year/{data.year}/">{data.year} graphics</a>
</p>
<h1 class="section-heading">{data.monthName} {data.year} graphics</h1>

<SearchBox bind:value={query} placeholder="Search by title or byline…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<div class="graphics-summary">
		{filtered.length.toLocaleString()} {filtered.length === 1 ? 'match' : 'matches'}
	</div>
	<PaginatedGraphicGrid graphics={filtered} label={`${data.monthName} ${data.year} graphics`} />
{/if}

<style>
	.breadcrumb {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
	}

	.graphics-summary {
		margin: 0.2rem 0 0.75rem;
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}
</style>
