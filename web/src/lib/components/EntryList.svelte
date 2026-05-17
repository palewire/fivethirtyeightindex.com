<script lang="ts">
	import { base } from '$app/paths';
	import { slugify } from '$lib/data';
	import type { Entry } from '$lib/types';

	interface Props {
		entries: Entry[];
		showByline?: boolean;
		sortable?: boolean;
	}

	type SortKey = 'date' | 'title' | 'byline';
	type SortDirection = 'asc' | 'desc';

	let { entries, showByline = true, sortable = false }: Props = $props();
	let sortKey = $state<SortKey | null>(null);
	let sortDirection = $state<SortDirection>('asc');

	const collator = new Intl.Collator(undefined, {
		numeric: true,
		sensitivity: 'base'
	});

	/** YYYY-MM-DD from full ISO timestamps; shorter dates (Blogspot-era
	 *  YYYY-MM) pass through unmodified. */
	function fmtDate(iso: string): string {
		if (!iso) return '';
		if (iso.length >= 10) return iso.slice(0, 10);
		return iso;
	}

	function displayByline(entry: Entry): string {
		if (entry.authors.length > 0) return entry.authors.join(', ');
		return entry.byline;
	}

	function sortValue(entry: Entry, key: SortKey): string {
		if (key === 'date') return entry.date ?? '';
		if (key === 'title') return entry.title;
		return displayByline(entry);
	}

	function toggleSort(key: SortKey): void {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
			return;
		}

		sortKey = key;
		sortDirection = key === 'date' ? 'desc' : 'asc';
	}

	let sortedEntries = $derived.by(() => {
		if (!sortable || !sortKey) return entries;

		const activeSort = sortKey;
		const sorted = [...entries];
		sorted.sort((left, right) => {
			const order = collator.compare(
				sortValue(left, activeSort),
				sortValue(right, activeSort)
			);
			return sortDirection === 'asc' ? order : -order;
		});
		return sorted;
	});
</script>

<table class="entries">
	<thead class:visually-hidden={!sortable}>
		<tr>
			<th
				scope="col"
				class="c-date"
				aria-sort={sortKey === 'date'
					? sortDirection === 'asc'
						? 'ascending'
						: 'descending'
					: 'none'}
			>
				{#if sortable}
					<button type="button" class="sort-button" onclick={() => toggleSort('date')}>
						Date
						<span class="sort-indicator" aria-hidden="true">
							{sortKey === 'date' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
						</span>
					</button>
				{:else}
					Date
				{/if}
			</th>
			<th
				scope="col"
				class="c-title"
				aria-sort={sortKey === 'title'
					? sortDirection === 'asc'
						? 'ascending'
						: 'descending'
					: 'none'}
			>
				{#if sortable}
					<button type="button" class="sort-button" onclick={() => toggleSort('title')}>
						Headline
						<span class="sort-indicator" aria-hidden="true">
							{sortKey === 'title' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
						</span>
					</button>
				{:else}
					Headline
				{/if}
			</th>
			{#if showByline}
				<th
					scope="col"
					class="c-byline"
					aria-sort={sortKey === 'byline'
						? sortDirection === 'asc'
							? 'ascending'
							: 'descending'
						: 'none'}
				>
					{#if sortable}
						<button type="button" class="sort-button" onclick={() => toggleSort('byline')}>
							Byline
							<span class="sort-indicator" aria-hidden="true">
								{sortKey === 'byline' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
							</span>
						</button>
					{:else}
						Byline
					{/if}
				</th>
			{/if}
		</tr>
	</thead>
	<tbody>
		{#each sortedEntries as entry (entry.id)}
			<tr>
				<td class="c-date">
					<time datetime={entry.date}>{fmtDate(entry.date)}</time>
				</td>
				<td class="c-title">
					<a href={entry.url} rel="noopener external" target="_blank">{entry.title}</a>
				</td>
				{#if showByline}<td class="c-byline">
					{#each entry.authors as name, i (name)}
						<a href="{base}/byline/{slugify(name)}/">{name}</a>{i < entry.authors.length - 1
							? ', '
							: ''}
					{/each}
					{#if entry.authors.length === 0 && entry.byline}{entry.byline}{/if}
				</td>{/if}
			</tr>
		{/each}
	</tbody>
</table>
