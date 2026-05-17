<script lang="ts">
	import { EntryList, SearchBox } from '$lib/components';
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
	<title>{data.monthName} {data.year} — fivethirtyeightindex.com</title>
</svelte:head>

<p class="breadcrumb">
	<a href="{base}/year/{data.year}/">{data.year}</a>
</p>
<h1 class="section-heading">{data.monthName} {data.year}</h1>

<SearchBox bind:value={query} placeholder="Search this month's entries…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<EntryList entries={filtered} sortable={true} />
{/if}

<style>
	.breadcrumb {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
	}
</style>
