<script lang="ts">
	import { EntryList, SearchBox } from '$lib/components';
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
	<title>{data.name} — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">
	{data.name}'s {data.entries.length.toLocaleString()}
	{data.entries.length === 1 ? 'byline' : 'bylines'}
</h1>

<SearchBox bind:value={query} placeholder="Search by headline…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<EntryList entries={filtered} showByline={false} sortable={true} />
{/if}
