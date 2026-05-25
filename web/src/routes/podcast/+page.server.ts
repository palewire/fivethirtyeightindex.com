import { loadPodcastsFromDisk } from '$lib/server/data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const cache = await loadPodcastsFromDisk();

	return {
		podcasts: cache.all,
		series: cache.series,
		total: cache.all.length
	};
};
