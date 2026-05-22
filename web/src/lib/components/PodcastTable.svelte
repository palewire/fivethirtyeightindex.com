<script lang="ts">
	import { base } from '$app/paths';
	import type { Podcast } from '$lib/types';

	interface Props {
		podcasts: Podcast[];
		showSeries?: boolean;
	}

	let { podcasts, showSeries = true }: Props = $props();

	type SortKey = 'date' | 'title' | 'series';
	type SortDirection = 'asc' | 'desc';

	let sortKey = $state<SortKey>('date');
	let sortDirection = $state<SortDirection>('asc');

	const textCollator = new Intl.Collator(undefined, { sensitivity: 'base' });

	function sortValue(podcast: Podcast, key: SortKey): string {
		if (key === 'date') return podcast.date || '￿';
		if (key === 'title') return podcast.title;
		return podcast.series;
	}

	function toggleSort(key: SortKey): void {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
			return;
		}

		sortKey = key;
		sortDirection = 'asc';
	}

	function sortLabel(label: string): string {
		return `Sort by ${label}, currently ${sortDirection === 'asc' ? 'ascending' : 'descending'}`;
	}

	let ordered = $derived.by(() => {
		const activeSort = sortKey;
		const decorated = podcasts.map((podcast) => ({
			podcast,
			value: sortValue(podcast, activeSort)
		}));
		decorated.sort((left, right) => {
			const order =
				activeSort === 'date'
					? left.value.localeCompare(right.value)
					: textCollator.compare(left.value, right.value);
			if (order !== 0) return sortDirection === 'asc' ? order : -order;
			return left.podcast.title.localeCompare(right.podcast.title);
		});
		return decorated.map(({ podcast }) => podcast);
	});

	function fmtDate(iso: string): string {
		if (!iso) return '';
		if (iso.length >= 10) return iso.slice(0, 10);
		return iso;
	}
</script>

<table class="entries">
	<thead>
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
				<button
					type="button"
					class="sort-button"
					aria-label={sortLabel('date')}
					onclick={() => toggleSort('date')}
				>
					Date
					<span class="sort-indicator" aria-hidden="true">
						{sortKey === 'date' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
					</span>
				</button>
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
				<button
					type="button"
					class="sort-button"
					aria-label={sortLabel('podcast')}
					onclick={() => toggleSort('title')}
				>
					Podcast
					<span class="sort-indicator" aria-hidden="true">
						{sortKey === 'title' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
					</span>
				</button>
			</th>
			{#if showSeries}<th
				scope="col"
				class="c-series"
				aria-sort={sortKey === 'series'
					? sortDirection === 'asc'
						? 'ascending'
						: 'descending'
					: 'none'}
			>
				<button
					type="button"
					class="sort-button"
					aria-label={sortLabel('series')}
					onclick={() => toggleSort('series')}
				>
					Series
					<span class="sort-indicator" aria-hidden="true">
						{sortKey === 'series' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}
					</span>
				</button>
			</th>{/if}
		</tr>
	</thead>
	<tbody>
		{#each ordered as podcast (podcast.id)}
			<tr>
				<td class="c-date">
					{#if podcast.date}<time datetime={podcast.date}>{fmtDate(podcast.date)}</time>{/if}
				</td>
				<td class="c-title">
					<a href={podcast.url} rel="noopener external" target="_blank">{podcast.title}</a>
				</td>
				{#if showSeries}<td class="c-series">
					<a href="{base}/podcast/{podcast.series_slug}/">{podcast.series}</a>
				</td>{/if}
			</tr>
		{/each}
	</tbody>
</table>
