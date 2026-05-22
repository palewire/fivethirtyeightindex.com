import { loadPodcasts } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadPodcasts(fetch);

	return {
		podcasts: cache.all,
		series: cache.series,
		total: cache.all.length
	};
};
