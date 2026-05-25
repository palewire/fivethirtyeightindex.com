import { error } from '@sveltejs/kit';
import { loadIllustrations } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}

	const cache = await loadIllustrations(fetch);
	const illustrations = cache.byYear.get(yearNum) ?? [];
	if (illustrations.length === 0) {
		error(404, `no illustrations for ${yearNum}`);
	}

	return {
		year: yearNum,
		illustrations,
		months: cache.monthsByYear.get(yearNum) ?? []
	};
};
