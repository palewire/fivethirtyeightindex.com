<script lang="ts">
	import { EntryList } from '$lib/components';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head>
	<title>{data.year} — fakethirtyeight.com</title>
</svelte:head>

<h1 class="section-heading">{data.year} ({data.entries.length.toLocaleString()} entries)</h1>
<EntryList entries={data.entries} />
