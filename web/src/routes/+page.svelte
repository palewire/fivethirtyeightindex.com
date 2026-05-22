<script lang="ts">
	import { afterNavigate } from '$app/navigation';
	import {
		BylineTeaser,
		DatasetTeaser,
		EntryList,
		PodcastTeaser,
		SearchBox,
		YearList
	} from '$lib/components';
	import { loadEntries } from '$lib/data';
	import { search, type SearchResult } from '$lib/search';
	import type { Entry } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');
	let results = $state<Entry[] | SearchResult[]>([]);
	let searched = $state(false);
	let allEntries: Entry[] | null = null;

	async function runSearch() {
		if (!query.trim()) {
			// Empty box: drop back to the default homepage state.
			searched = false;
			results = [];
			return;
		}
		if (!allEntries) {
			const cache = await loadEntries();
			allEntries = cache.all;
		}
		results = search(allEntries, query, { limit: 100 });
		searched = true;
	}

	// Reset search state on any navigation TO the homepage (e.g. clicking
	// the header brand link while already on /). Skip the initial mount.
	afterNavigate(({ from }) => {
		if (!from) return;
		query = '';
		searched = false;
		results = [];
	});
</script>

<svelte:head>
	<title>fivethirtyeightindex</title>
</svelte:head>

<SearchBox bind:value={query} placeholder="Search title or byline…" oninput={runSearch} />

{#if searched}
	{#if results.length === 0}
		<p class="no-results">No matches.</p>
	{:else}
		<h2 class="section-heading">Results ({results.length})</h2>
		<EntryList entries={results as Entry[]} sortable={true} />
	{/if}
{:else}
	<div class="opening-teaser">
		<EntryList entries={data.opening} />
	</div>
{/if}

{#if !searched}
	<YearList years={data.years} />
	<BylineTeaser bylines={data.topBylines} total={data.totalBylines} />
	<PodcastTeaser series={data.podcastSeries} total={data.totalPodcasts} />
	<DatasetTeaser datasets={data.datasets} total={data.totalDatasets} />
{/if}
