/** MiniSearch wrapper: lazy-build the index once per page lifecycle. */
import MiniSearch from 'minisearch';
import type { Entry } from './types';

let searchIndex: MiniSearch<Entry> | null = null;

export function buildIndex(entries: Entry[]): MiniSearch<Entry> {
	if (searchIndex) return searchIndex;
	const mini = new MiniSearch<Entry>({
		idField: 'id',
		fields: ['title', 'byline'],
		storeFields: ['id', 'title', 'byline', 'authors', 'year', 'date', 'kind', 'url'],
		searchOptions: {
			boost: { title: 2 },
			prefix: true,
			fuzzy: 0.15,
			combineWith: 'AND'
		}
	});
	mini.addAll(entries);
	searchIndex = mini;
	return mini;
}

export interface SearchResult extends Entry {
	score: number;
}

export function search(
	entries: Entry[],
	query: string,
	options: { kinds?: Set<string>; limit?: number } = {}
): SearchResult[] {
	const idx = buildIndex(entries);
	const limit = options.limit ?? 100;
	const kinds = options.kinds;

	const trimmed = query.trim();
	if (!trimmed) {
		// No query: just filter + slice the cached entry array.
		const filtered = kinds && kinds.size > 0 ? entries.filter((e) => kinds.has(e.kind)) : entries;
		return filtered.slice(0, limit).map((e) => ({ ...e, score: 0 }));
	}

	const hits = idx.search(trimmed) as unknown as Array<Entry & { score: number }>;
	const filtered = kinds && kinds.size > 0 ? hits.filter((h) => kinds.has(h.kind)) : hits;
	filtered.sort((a, b) => (a.date ?? '').localeCompare(b.date ?? ''));
	return filtered.slice(0, limit);
}
