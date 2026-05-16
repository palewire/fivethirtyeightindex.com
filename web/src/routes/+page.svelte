<script lang="ts">
	import {
		BylineTeaser,
		EntryList,
		SearchBox,
		Tagline,
		YearList
	} from '$lib/components';
	import { loadEntries } from '$lib/data';
	import { search, type SearchResult } from '$lib/search';
	import type { Entry } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let query = $state('');
	let kindFilters = $state(
		new Map<string, boolean>([
			['article', true],
			['liveblog', true],
			['project', true],
			['podcast', true],
			['video', true],
			['methodology', true]
		])
	);

	let results = $state<Entry[] | SearchResult[]>([]);
	let searched = $state(false);
	let allEntries: Entry[] | null = null;

	async function ensureLoaded() {
		if (allEntries) return allEntries;
		const cache = await loadEntries();
		allEntries = cache.all;
		return allEntries;
	}

	async function rerunSearch() {
		const entries = await ensureLoaded();
		const enabled = new Set(
			[...kindFilters.entries()].filter(([, on]) => on).map(([k]) => k)
		);
		results = search(entries, query, { kinds: enabled, limit: 100 });
		searched = true;
	}
</script>

<svelte:head>
	<title>fivethirtyeightindex.com</title>
</svelte:head>

<Tagline total={data.total} />

<SearchBox bind:query bind:kindFilters oninput={rerunSearch} />

{#if searched}
	{#if results.length === 0}
		<p class="no-results">No matches.</p>
	{:else}
		<h2 class="section-heading">Results ({results.length})</h2>
		<EntryList entries={results as Entry[]} />
	{/if}
{:else}
	<EntryList entries={data.opening} />
	<p class="muted">
		Showing the first {data.opening.length} entries chronologically. Browse a specific
		year below, or search above.
	</p>
{/if}

{#if !searched}
	<YearList years={data.years} />
	<BylineTeaser bylines={data.topBylines} total={data.totalBylines} />
{/if}
