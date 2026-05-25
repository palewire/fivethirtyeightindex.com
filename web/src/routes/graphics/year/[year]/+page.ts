import { error } from '@sveltejs/kit';
import { loadGraphics } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}

	const cache = await loadGraphics(fetch);
	const graphics = cache.byYear.get(yearNum) ?? [];
	if (graphics.length === 0) {
		error(404, `no graphics for ${yearNum}`);
	}

	return {
		year: yearNum,
		graphics,
		months: cache.monthsByYear.get(yearNum) ?? []
	};
};
