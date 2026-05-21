import { loadDatasets } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadDatasets(fetch);

	return {
		datasets: cache.all,
		total: cache.all.length
	};
};
