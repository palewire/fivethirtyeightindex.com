import {
	loadDatasetsFromDisk,
	loadEntriesFromDisk,
	loadGraphicsFromDisk,
	loadIllustrationsFromDisk,
	loadPodcastsFromDisk
} from '$lib/server/data';
import type { Graphic } from '$lib/types';
import type { PageServerLoad } from './$types';

function randomGraphics(graphics: Graphic[], limit = 10): Graphic[] {
	return [...graphics]
		.map((graphic) => ({ graphic, key: Math.random() }))
		.sort((a, b) => a.key - b.key)
		.slice(0, limit)
		.map(({ graphic }) => graphic);
}

export const load: PageServerLoad = async () => {
	const cache = await loadEntriesFromDisk();
	const datasetCache = await loadDatasetsFromDisk();
	const podcastCache = await loadPodcastsFromDisk();
	const graphicCache = await loadGraphicsFromDisk();
	const illustrationCache = await loadIllustrationsFromDisk();

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
		opening: cache.all.slice(0, 10)
	};
};
