import { loadGraphics } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadGraphics(fetch);

	return {
		graphics: cache.all,
		categories: cache.categories,
		years: cache.years,
		total: cache.all.length
	};
};
