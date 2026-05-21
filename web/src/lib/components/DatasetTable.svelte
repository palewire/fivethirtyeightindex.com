<script lang="ts">
	import type { Dataset } from '$lib/types';

	interface Props {
		datasets: Dataset[];
	}

	let { datasets }: Props = $props();

	function hrefFor(dataset: Dataset): string {
		return dataset.archive_url || dataset.dataset_url;
	}

	function fmtDate(iso: string): string {
		if (!iso) return '';
		if (iso.length >= 10) return iso.slice(0, 10);
		return iso;
	}
</script>

<table class="entries">
	<thead class="visually-hidden">
		<tr>
			<th scope="col">Date</th>
			<th scope="col">Dataset</th>
		</tr>
	</thead>
	<tbody>
		{#each datasets as dataset (dataset.id)}
			<tr>
				<td class="c-date">
					{#if dataset.date}<time datetime={dataset.date}>{fmtDate(dataset.date)}</time>{/if}
				</td>
				<td class="c-title">
					<a href={hrefFor(dataset)} rel="noopener external" target="_blank"
						>{dataset.title}</a
					>
				</td>
			</tr>
		{/each}
	</tbody>
</table>
