<script lang="ts">
	import { base } from '$app/paths';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Group by first letter (A–Z, 0–9, other).
	function bucketKey(name: string): string {
		const ch = name.charAt(0).toUpperCase();
		if (/[A-Z]/.test(ch)) return ch;
		if (/[0-9]/.test(ch)) return '#';
		return '·';
	}

	let buckets = $derived.by(() => {
		const map = new Map<string, typeof data.bylines>();
		for (const b of data.bylines) {
			const k = bucketKey(b.name);
			let arr = map.get(k);
			if (!arr) {
				arr = [];
				map.set(k, arr);
			}
			arr.push(b);
		}
		return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
	});
</script>

<svelte:head>
	<title>Bylines — fakethirtyeight.com</title>
</svelte:head>

<h1 class="section-heading">Bylines ({data.total.toLocaleString()})</h1>

<p class="muted">
	Every byline that appeared on at least one fivethirtyeight.com entry. Click a name to see
	their work in chronological order.
</p>

{#each buckets as [letter, group] (letter)}
	<h2 class="section-heading">{letter}</h2>
	<p class="browse-list">
		{#each group as b (b.slug)}
			<a href="{base}/byline/{b.slug}/">{b.name}</a><span class="count"> ({b.count})</span>
		{/each}
	</p>
{/each}
