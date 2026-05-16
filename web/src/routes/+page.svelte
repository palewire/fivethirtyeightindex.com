<script lang="ts">
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		BylineTeaser,
		EntryList,
		SearchBox,
		YearList
	} from '$lib/components';
	import { loadEntries } from '$lib/data';
	import { search, type SearchResult } from '$lib/search';
	import type { Entry } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The active search query is stored in the URL as `?q=…`. That makes the
	// header-link return to `/` clear the search for free, and lets a clean
	// search box render the default homepage state. SvelteKit forbids
	// reading url.searchParams during prerender, so we start blank and
	// hydrate from the URL on first client tick.
	let query = $state('');

	let results = $state<Entry[] | SearchResult[]>([]);
	let allEntries: Entry[] | null = null;

	// URL → local state (initial hydrate + back/forward + header click).
	$effect(() => {
		if (!browser) return;
		const fromUrl = page.url.searchParams.get('q') ?? '';
		if (fromUrl !== query) query = fromUrl;
	});

	// Local state → URL + run search.
	$effect(() => {
		const q = query;
		if (browser) {
			const next = new URL(page.url);
			if (q) next.searchParams.set('q', q);
			else next.searchParams.delete('q');
			if (next.toString() !== page.url.toString()) {
				goto(next, { replaceState: true, noScroll: true, keepFocus: true });
			}
		}
		void runSearch(q);
	});

	async function runSearch(q: string) {
		if (!q.trim()) {
			results = [];
			return;
		}
		if (!allEntries) {
			const cache = await loadEntries();
			allEntries = cache.all;
		}
		results = search(allEntries, q, { limit: 100 });
	}

	let searched = $derived(query.trim() !== '');
</script>

<svelte:head>
	<title>fivethirtyeightindex</title>
</svelte:head>

<SearchBox bind:value={query} placeholder="Search title or byline…" />

{#if searched}
	{#if results.length === 0}
		<p class="no-results">No matches.</p>
	{:else}
		<h2 class="section-heading">Results ({results.length})</h2>
		<EntryList entries={results as Entry[]} />
	{/if}
{:else}
	<EntryList entries={data.opening} />
{/if}

{#if !searched}
	<YearList years={data.years} />
	<BylineTeaser bylines={data.topBylines} total={data.totalBylines} />
{/if}
