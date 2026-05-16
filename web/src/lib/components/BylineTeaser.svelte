<script lang="ts">
	import { base } from '$app/paths';

	interface Byline {
		slug: string;
		name: string;
		count: number;
	}

	interface Props {
		bylines: Byline[];
		total: number;
	}
	let { bylines, total }: Props = $props();
</script>

<h2 class="section-heading">Browse by byline</h2>
<div class="browse-block">
	<p class="browse-list">
		{#each bylines as b (b.slug)}
			<a href="{base}/byline/{b.slug}/">{b.name}</a><span class="count"> ({b.count})</span>
		{/each}
	</p>
	<p>
		<a href="{base}/byline/">See all {total.toLocaleString()} bylines →</a>
	</p>
</div>
