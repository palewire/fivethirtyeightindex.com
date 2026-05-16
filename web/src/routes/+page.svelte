<script lang="ts">
	import { base } from '$app/paths';
	import EntryList from '$lib/EntryList.svelte';
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

	// Seed results with an empty array; the homepage shows `data.recent`
	// below until the user types a query.
	let results = $state<Entry[] | SearchResult[]>([]);
	let allEntries: Entry[] | null = null;
	let searched = $state(false);

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

	function onInput(event: Event) {
		query = (event.target as HTMLInputElement).value;
		void rerunSearch();
	}

	function toggleKind(kind: string) {
		kindFilters.set(kind, !kindFilters.get(kind));
		kindFilters = new Map(kindFilters);
		void rerunSearch();
	}
</script>

<svelte:head>
	<title>fakethirtyeight.com — index</title>
</svelte:head>

<p class="tagline">
	An index of every fivethirtyeight.com article, liveblog, project, video, podcast, and
	methodology page preserved by the Internet Archive. {data.total.toLocaleString()} entries.
</p>

<form class="search" onsubmit={(e) => e.preventDefault()}>
	<input
		type="search"
		placeholder="Search title or byline…"
		bind:value={query}
		oninput={onInput}
		autocomplete="off"
		spellcheck="false"
	/>
	<div class="filters">
		{#each [...kindFilters.keys()] as kind (kind)}
			<label>
				<input
					type="checkbox"
					checked={kindFilters.get(kind)}
					onchange={() => toggleKind(kind)}
				/>{kind}
			</label>
		{/each}
	</div>
</form>

{#if searched}
	{#if results.length === 0}
		<p class="no-results">No matches.</p>
	{:else}
		<h2 class="section-heading">Results ({results.length})</h2>
		<EntryList entries={results as Entry[]} />
	{/if}
{:else}
	<h2 class="section-heading">Recent</h2>
	<EntryList entries={data.recent} />
{/if}

{#if !searched}
	<h2 class="section-heading">Browse by year</h2>
	<div class="browse-block">
		<p class="browse-list">
			{#each data.years as year (year)}
				<a href="{base}/year/{year}/">{year}</a>
			{/each}
		</p>
	</div>

	<h2 class="section-heading">
		Browse by byline <span class="count"
			>(top {data.topBylines.length} of {data.totalBylines.toLocaleString()})</span
		>
	</h2>
	<div class="browse-block">
		<p class="browse-list">
			{#each data.topBylines as b (b.slug)}
				<a href="{base}/byline/{b.slug}/">{b.name}</a><span class="count"> ({b.count})</span>
			{/each}
		</p>
	</div>
{/if}
