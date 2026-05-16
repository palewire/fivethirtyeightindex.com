<script lang="ts">
	import type { Entry } from './types';
	import { base } from '$app/paths';
	import { slugify } from './data';

	interface Props {
		entries: Entry[];
		showKind?: boolean;
	}

	let { entries, showKind = true }: Props = $props();

	function fmtDate(iso: string): string {
		if (!iso) return '';
		if (iso.length >= 10) return iso.slice(0, 10);
		return iso;
	}
</script>

<table class="entries">
	<tbody>
		{#each entries as entry (entry.id)}
			<tr>
				<td class="c-date">
					<time datetime={entry.date}>{fmtDate(entry.date)}</time>
				</td>
				<td class="c-title">
					<a href={entry.url} rel="noopener external" target="_blank">{entry.title}</a>
					{#if showKind && entry.kind !== 'article'}
						<em class="c-kind">[{entry.kind}]</em>
					{/if}
				</td>
				<td class="c-byline">
					{#each entry.authors as name, i (name)}
						<a href="{base}/byline/{slugify(name)}/">{name}</a>{i < entry.authors.length - 1
							? ', '
							: ''}
					{/each}
					{#if entry.authors.length === 0 && entry.byline}{entry.byline}{/if}
				</td>
			</tr>
		{/each}
	</tbody>
</table>
