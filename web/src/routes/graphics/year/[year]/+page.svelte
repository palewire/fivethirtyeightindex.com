<script lang="ts">
	import { PaginatedGraphicGrid, SearchBox } from '$lib/components';
	import { categoryLabel, graphicCategoryGroup, monthLabel } from '$lib/data';
	import { base } from '$app/paths';
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

	let categories = $derived.by(() => {
		const byCategory = new Map<string, number>();
		for (const graphic of data.graphics) {
			const slug = graphicCategoryGroup(graphic.category);
			byCategory.set(slug, (byCategory.get(slug) ?? 0) + 1);
		}
		return [...byCategory.entries()]
			.map(([slug, count]) => ({ slug, name: categoryLabel(slug), count }))
			.sort((a, b) => {
				if (a.slug === 'infographic' && b.slug !== 'infographic') return 1;
				if (b.slug === 'infographic' && a.slug !== 'infographic') return -1;
				return a.name.localeCompare(b.name);
			});
	});
</script>

<svelte:head>
	<title>{data.year} graphics — fivethirtyeightindex.com</title>
</svelte:head>

<p class="eyebrow"><a href="{base}/graphics/">All graphics</a></p>
<h1 class="section-heading">{data.year} graphics</h1>

{#if data.months.length > 1}
	<nav class="month-nav" aria-label="Jump to a month">
		{#each data.months as ym (ym)}
			<a href="{base}/graphics/year/{data.year}/{ym.slice(5)}/">{monthLabel(ym.slice(5))}</a>
		{/each}
	</nav>
{/if}

{#if categories.length > 1}
	<nav class="category-nav" aria-label="Filter graphics by type">
		<button class:active={category === ''} type="button" onclick={() => (category = '')}>
			All
		</button>
		{#each categories as c (c.slug)}
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
	<p class="no-results">No matches.</p>
{:else}
	<div class="graphics-summary">
		{filtered.length.toLocaleString()} {filtered.length === 1 ? 'match' : 'matches'}
	</div>
	<PaginatedGraphicGrid graphics={filtered} label={`${data.year} graphics`} />
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
