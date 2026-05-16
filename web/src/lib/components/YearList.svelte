<script lang="ts">
	import { base } from '$app/paths';

	interface Props {
		years: number[];
	}
	let { years }: Props = $props();
</script>

<h2 class="section-heading">Browse by year</h2>
<div class="browse-block">
	<ul class="browse-list">
		{#each years as year (year)}
			<li><a href="{base}/year/{year}/">{year}</a></li>
		{/each}
	</ul>
</div>
