import { loadGraphicsFromDisk } from '$lib/server/data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const cache = await loadGraphicsFromDisk();

	return {
		graphics: cache.all,
		categories: cache.categories,
		years: cache.years,
		total: cache.all.length
	};
};
