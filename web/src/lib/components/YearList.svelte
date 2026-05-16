<script lang="ts">
	import { base } from '$app/paths';

	interface Props {
		years: number[];
	}
	let { years }: Props = $props();
</script>

<h2 class="section-heading">Browse by year</h2>
<div class="browse-block">
	<p class="browse-list">
		{#each years as year (year)}
			<a href="{base}/year/{year}/">{year}</a>
		{/each}
	</p>
</div>
