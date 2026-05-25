import { loadIllustrations } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadIllustrations(fetch);

	return {
		illustrations: cache.all,
		years: cache.years,
		total: cache.all.length
	};
};
