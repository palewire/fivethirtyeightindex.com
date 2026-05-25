<script lang="ts">
	import { base } from '$app/paths';

	interface Props {
		years: number[];
		hrefPrefix?: string;
		showHeading?: boolean;
	}
	let { years, hrefPrefix = '/year', showHeading = true }: Props = $props();
</script>

{#if showHeading}
	<h2 class="section-heading">Browse by year</h2>
{/if}
<div class="browse-block">
	<ul class="browse-list">
		{#each years as year (year)}
			<li><a href="{base}{hrefPrefix}/{year}/">{year}</a></li>
		{/each}
	</ul>
</div>
