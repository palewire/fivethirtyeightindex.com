<script lang="ts">
	import { BylineTable, SearchBox } from '$lib/components';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.bylines;
		return data.bylines.filter((b) => b.name.toLowerCase().includes(q));
	});
</script>

<svelte:head>
	<title>Bylines — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.total.toLocaleString()} bylines</h1>

<SearchBox bind:value={query} placeholder="Search bylines…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<BylineTable bylines={filtered} />
{/if}
