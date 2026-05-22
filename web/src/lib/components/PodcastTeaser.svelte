<script lang="ts">
	import { base } from '$app/paths';

	interface PodcastSeries {
		slug: string;
		name: string;
		count: number;
	}

	interface Props {
		series: PodcastSeries[];
		total: number;
	}

	let { series, total }: Props = $props();
</script>

<h2 class="section-heading">Podcasts</h2>
<div class="browse-block">
	<ul class="browse-list">
		{#each series as s (s.slug)}
			<li>
				<a href="{base}/podcast/{s.slug}/">{s.name}</a><span class="count"
					>({s.count})</span
				>
			</li>
		{/each}
		<li class="see-all">
			<a href="{base}/podcast/">See all {total.toLocaleString()} podcasts -></a>
		</li>
	</ul>
</div>

<style>
	.see-all a {
		color: var(--color-fg);
		font-weight: var(--font-weight-regular);
	}

	.see-all a:hover {
		color: var(--color-link);
		text-decoration: underline;
	}
</style>
