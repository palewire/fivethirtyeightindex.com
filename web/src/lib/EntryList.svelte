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
		// Display YYYY-MM-DD when we have a full timestamp; otherwise the
		// raw fallback (e.g. YYYY-MM from old WP era).
		if (iso.length >= 10) return iso.slice(0, 10);
		return iso;
	}
</script>

<ol class="entries">
	{#each entries as entry (entry.id)}
		<li>
			<a href={entry.url} rel="noopener external" target="_blank">{entry.title}</a>
			<div class="meta">
				{#if showKind}<span class="kind">{entry.kind}</span> · {/if}
				{#if entry.date}<span>{fmtDate(entry.date)}</span>{/if}
				{#if entry.byline}
					· by
					{#each entry.authors as name, i (name)}
						<a href="{base}/byline/{slugify(name)}/">{name}</a>{i < entry.authors.length - 1
							? ', '
							: ''}
					{/each}
					{#if entry.authors.length === 0}{entry.byline}{/if}
				{/if}
				{#if entry.year != null}
					· <a href="{base}/year/{entry.year}/">{entry.year}</a>
				{/if}
			</div>
		</li>
	{/each}
</ol>
