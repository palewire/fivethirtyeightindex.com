import { loadDatasets, loadEntries } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadEntries(fetch);
	const datasetCache = await loadDatasets(fetch);

	// Top byline buckets (most prolific authors)
	const byByline = [...cache.byBylineSlug.entries()]
		.map(([slug, { name, entries }]) => ({ slug, name, count: entries.length }))
		.sort((a, b) => b.count - a.count);

	return {
		total: cache.all.length,
		years: cache.years,
		topBylines: byByline.slice(0, 10),
		totalBylines: byByline.length,
		datasets: datasetCache.all.slice(0, 40),
		totalDatasets: datasetCache.all.length,
		// `cache.all` is sorted oldest-first; the first slice is "from the
		// beginning" — appropriate for a retrospective. Kept short so the
		// homepage stays scannable; year pages are the deep-dive.
		opening: cache.all.slice(0, 10)
	};
};
