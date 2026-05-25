import { loadIllustrationsFromDisk } from '$lib/server/data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const cache = await loadIllustrationsFromDisk();

	return {
		illustrations: cache.all,
		years: cache.years,
		total: cache.all.length
	};
};
