<script lang="ts">
	import { PodcastTable, SearchBox } from '$lib/components';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.podcasts;
		return data.podcasts.filter((podcast) => {
			return podcast.title.toLowerCase().includes(q) || podcast.date.includes(q);
		});
	});
</script>

<svelte:head>
	<title>{data.name} — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.name}</h1>

<SearchBox bind:value={query} placeholder="Search this series…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<PodcastTable podcasts={filtered} showSeries={false} />
{/if}
