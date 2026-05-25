<script lang="ts">
	import { base } from '$app/paths';
	import { slugify } from '$lib/data';
	import type { Entry } from '$lib/types';

	interface Props {
		entries: Entry[];
		showByline?: boolean;
		sortable?: boolean;
		pageSize?: number | null;
	}

	type SortKey = 'date' | 'title' | 'byline';
	type SortDirection = 'asc' | 'desc';

	let { entries, showByline = true, sortable = false, pageSize = null }: Props = $props();
	let sortKey = $state<SortKey | null>(null);
	let sortDirection = $state<SortDirection>('asc');
	// svelte-ignore state_referenced_locally
	let visibleCount = $state(pageSize ?? Number.POSITIVE_INFINITY);

	const textCollator = new Intl.Collator(undefined, { sensitivity: 'base' });

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

	function sortLabel(label: string, key: SortKey): string {
		if (sortKey !== key) return `Sort by ${label}, currently unsorted`;
		return `Sort by ${label}, currently ${sortDirection === 'asc' ? 'ascending' : 'descending'}`;
	}

	let sortedEntries = $derived.by(() => {
		if (!sortable || !sortKey) return entries;

		const activeSort = sortKey;
		const decorated = entries.map((entry) => ({
			entry,
			value: sortValue(entry, activeSort)
		}));
		decorated.sort((left, right) => {
			const order =
				activeSort === 'date'
					? left.value.localeCompare(right.value)
					: textCollator.compare(left.value, right.value);
			return sortDirection === 'asc' ? order : -order;
		});
		return decorated.map(({ entry }) => entry);
	});

	let visibleEntries = $derived(sortedEntries.slice(0, visibleCount));
	let hasMore = $derived(visibleCount < sortedEntries.length);

	$effect(() => {
		entries;
		visibleCount = pageSize ?? Number.POSITIVE_INFINITY;
	});

	function showMore(): void {
		if (!pageSize) return;
		visibleCount = Math.min(visibleCount + pageSize, sortedEntries.length);
	}
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
					<button
						type="button"
						class="sort-button"
						aria-label={sortLabel('date', 'date')}
						onclick={() => toggleSort('date')}
					>
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
					<button
						type="button"
						class="sort-button"
						aria-label={sortLabel('headline', 'title')}
						onclick={() => toggleSort('title')}
					>
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
						<button
							type="button"
							class="sort-button"
							aria-label={sortLabel('byline', 'byline')}
							onclick={() => toggleSort('byline')}
						>
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
		{#each visibleEntries as entry (entry.id)}
			<tr>
				<td class="c-date">
					<time datetime={entry.date}>{fmtDate(entry.date)}</time>
				</td>
				<td class="c-title">
					<a href={entry.url} rel="noopener external" target="_blank">{entry.title}</a>
				</td>
				{#if showByline}<td class="c-byline">
					{#if entry.authors.length > 0}
						{#each entry.authors as name, i (name)}
							<a href="{base}/byline/{slugify(name)}/">{name}</a>{i < entry.authors.length - 1
								? ', '
								: ''}
						{/each}
					{:else}
						{displayByline(entry)}
					{/if}
				</td>{/if}
			</tr>
		{/each}
	</tbody>
</table>

{#if hasMore}
	<div class="pagination">
		<p>
			Showing {visibleEntries.length.toLocaleString()} of {sortedEntries.length.toLocaleString()}
		</p>
		<button type="button" onclick={showMore}>Load more</button>
	</div>
{/if}

<style>
	.pagination {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.45rem;
		margin: 0.5rem 0 var(--space-lg);
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}

	.pagination p {
		margin: 0;
	}

	.pagination button {
		border: var(--rule-thin);
		border-radius: 4px;
		background: var(--color-bg);
		color: var(--color-link);
		font: inherit;
		padding: 0.3rem 0.55rem;
		cursor: pointer;
	}

	.pagination button:hover {
		color: var(--color-fg);
		border-color: var(--color-fg);
	}
</style>
