<script lang="ts">
	import { base } from '$app/paths';
	import type { Dataset } from '$lib/types';

	interface Props {
		datasets: Dataset[];
		total: number;
	}

	let { datasets, total }: Props = $props();
	let featured = $derived(datasets.slice(0, 10));

	function hrefFor(dataset: Dataset): string {
		return dataset.archive_url || dataset.dataset_url;
	}
</script>

<h2 class="section-heading">Datasets</h2>
<div class="browse-block dataset-panel">
	<ul class="browse-list">
		{#each featured as dataset (dataset.id)}
			<li>
				<a href={hrefFor(dataset)} rel="noopener external" target="_blank"
					>{dataset.title}</a
				>
			</li>
		{/each}
		<li class="see-all">
			<a href="{base}/dataset/">See all {total.toLocaleString()} datasets -></a>
		</li>
	</ul>
</div>

<style>
	.dataset-panel {
		padding-bottom: var(--space-md);
	}

	.dataset-panel li a {
		color: var(--color-muted);
	}

	.dataset-panel li a:hover {
		color: var(--color-fg);
		text-decoration: underline;
	}

	.dataset-panel .see-all a {
		color: var(--color-fg);
		font-weight: var(--font-weight-regular);
	}

	.dataset-panel .see-all a:hover {
		color: var(--color-link);
	}
</style>
