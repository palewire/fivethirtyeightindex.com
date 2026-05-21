<script lang="ts">
	import { EntryList, SearchBox } from '$lib/components';
	import { monthLabel } from '$lib/data';
	import { base } from '$app/paths';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.entries;
		return data.entries.filter(
			(e) =>
				e.title.toLowerCase().includes(q) ||
				e.byline.toLowerCase().includes(q)
		);
	});
</script>

<svelte:head>
	<title>{data.year} — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.year}</h1>

{#if data.months.length > 1}
	<nav class="month-nav" aria-label="Jump to a month">
		{#each data.months as ym (ym)}
			<a href="{base}/year/{data.year}/{ym.slice(5)}/">{monthLabel(ym.slice(5))}</a>
		{/each}
	</nav>
{/if}

<SearchBox bind:value={query} placeholder="Search this year's entries…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<EntryList entries={filtered} sortable={true} />
{/if}

<style>
	.month-nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.75rem;
		margin: 0 0 0.75rem;
		font-size: 0.9rem;
	}
</style>
