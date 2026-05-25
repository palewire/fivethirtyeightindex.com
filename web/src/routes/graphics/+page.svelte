<script lang="ts">
	import { PaginatedGraphicGrid, SearchBox, YearList } from '$lib/components';
	import { graphicCategoryGroup } from '$lib/data';
	import type { Graphic } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');
	let category = $state('');

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
		return data.graphics.filter((graphic) => {
			return (
				(!category || graphicCategoryGroup(graphic.category) === category) &&
				(!q || matches(graphic, q))
			);
		});
	});
</script>

<svelte:head>
	<title>Graphics — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.total.toLocaleString()} graphics</h1>

<YearList years={data.years} hrefPrefix="/graphics/year" showHeading={false} />

{#if data.categories.length > 1}
	<nav class="category-nav" aria-label="Filter graphics by type">
		<button class:active={category === ''} type="button" onclick={() => (category = '')}>
			All
		</button>
		{#each data.categories as c (c.slug)}
			<button
				class:active={category === c.slug}
				type="button"
				onclick={() => (category = c.slug)}
			>
				{c.name} <span>{c.count.toLocaleString()}</span>
			</button>
		{/each}
	</nav>
{/if}

<SearchBox bind:value={query} placeholder="Search by title or byline…" />

{#if filtered.length === 0}
	<p class="no-results">
		{data.total === 0 ? 'No uploaded graphics yet.' : 'No matches.'}
	</p>
{:else}
	<div class="graphics-summary">
		{filtered.length.toLocaleString()} {filtered.length === 1 ? 'match' : 'matches'}
	</div>
	<PaginatedGraphicGrid graphics={filtered} label="Graphics" />
{/if}

<style>
	.category-nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem 0.5rem;
		margin: 0 0 0.75rem;
		font-size: var(--font-size-table);
	}

	.category-nav button {
		border: var(--rule-thin);
		border-radius: 4px;
		background: var(--color-bg);
		color: var(--color-link);
		font: inherit;
		padding: 0.25rem 0.45rem;
		cursor: pointer;
	}

	.category-nav button:hover,
	.category-nav button.active {
		color: var(--color-fg);
		border-color: var(--color-fg);
	}

	.category-nav span {
		color: var(--color-muted);
	}

	.graphics-summary {
		margin: 0.2rem 0 0.75rem;
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}

</style>
