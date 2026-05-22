<script lang="ts">
	import { base } from '$app/paths';
	import { PodcastTable, SearchBox } from '$lib/components';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');

	let filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.podcasts;
		return data.podcasts.filter((podcast) => {
			return (
				podcast.title.toLowerCase().includes(q) ||
				podcast.series.toLowerCase().includes(q) ||
				podcast.date.includes(q)
			);
		});
	});
</script>

<svelte:head>
	<title>Podcasts — fivethirtyeightindex.com</title>
</svelte:head>

<h1 class="section-heading">{data.total.toLocaleString()} podcasts</h1>

{#if data.series.length > 1}
	<nav class="month-nav" aria-label="Jump to a podcast series">
		{#each data.series as series (series.slug)}
			<a href="{base}/podcast/{series.slug}/">{series.name}</a>
		{/each}
	</nav>
{/if}

<SearchBox bind:value={query} placeholder="Search podcasts…" />

{#if filtered.length === 0}
	<p class="no-results">No matches.</p>
{:else}
	<PodcastTable podcasts={filtered} />
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
