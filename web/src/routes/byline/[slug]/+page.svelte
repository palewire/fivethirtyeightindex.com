<script lang="ts">
	import EntryList from '$lib/EntryList.svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head>
	<title>{data.name} — fakethirtyeight.com</title>
</svelte:head>

<h1 class="section-heading">{data.name} ({data.entries.length.toLocaleString()} entries)</h1>
<EntryList entries={data.entries} />
