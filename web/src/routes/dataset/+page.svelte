<script lang="ts">
	import { DatasetTable, SearchBox } from '$lib/components';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.datasets;
		return data.datasets.filter((dataset) => dataset.title.toLowerCase().includes(q));
	});
</script>

<svelte:head>
	<title>Datasets — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.total.toLocaleString()} datasets</h1>

<SearchBox bind:value={query} placeholder="Search datasets…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<DatasetTable datasets={filtered} />
{/if}
