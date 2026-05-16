import { loadEntries } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadEntries(fetch);

	// Top byline buckets (most prolific authors)
	const byByline = [...cache.byBylineSlug.entries()]
		.map(([slug, { name, entries }]) => ({ slug, name, count: entries.length }))
		.sort((a, b) => b.count - a.count);

	return {
		total: cache.all.length,
		years: cache.years,
		topBylines: byByline.slice(0, 40),
		totalBylines: byByline.length,
		// `cache.all` is sorted oldest-first; the first slice is "from the
		// beginning" — appropriate for a retrospective.
		opening: cache.all.slice(0, 50)
	};
};
