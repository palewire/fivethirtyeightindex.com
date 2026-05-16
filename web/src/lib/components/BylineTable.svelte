<script lang="ts">
	import { base } from '$app/paths';

	interface BylineRow {
		slug: string;
		name: string;
		count: number;
	}

	interface Props {
		bylines: BylineRow[];
	}

	let { bylines }: Props = $props();
</script>

<table class="entries">
	<thead class="visually-hidden">
		<tr>
			<th scope="col">Byline</th>
			<th scope="col">Entries</th>
		</tr>
	</thead>
	<tbody>
		{#each bylines as b (b.slug)}
			<tr>
				<td class="c-title">
					<a href="{base}/byline/{b.slug}/">{b.name}</a>
				</td>
				<td class="c-byline">{b.count.toLocaleString()}</td>
			</tr>
		{/each}
	</tbody>
</table>
