import {
	loadDatasets,
	loadEntries,
	loadGraphics,
	loadIllustrations,
	loadPodcasts
} from '$lib/data';
import type { Graphic } from '$lib/types';
import type { PageLoad } from './$types';

function randomGraphics(graphics: Graphic[], limit = 10): Graphic[] {
	return [...graphics]
		.map((graphic) => ({ graphic, key: Math.random() }))
		.sort((a, b) => a.key - b.key)
		.slice(0, limit)
		.map(({ graphic }) => graphic);
}

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadEntries(fetch);
	const datasetCache = await loadDatasets(fetch);
	const podcastCache = await loadPodcasts(fetch);
	const graphicCache = await loadGraphics(fetch);
	const illustrationCache = await loadIllustrations(fetch);

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
		podcastSeries: podcastCache.series,
		totalPodcasts: podcastCache.all.length,
		graphics: randomGraphics(graphicCache.all),
		totalGraphics: graphicCache.all.length,
		illustrations: randomGraphics(illustrationCache.all),
		totalIllustrations: illustrationCache.all.length,
		// `cache.all` is sorted oldest-first; the first slice is "from the
		// beginning" — appropriate for a retrospective. Kept short so the
		// homepage stays scannable; year pages are the deep-dive.
		opening: cache.all.slice(0, 10)
	};
};
