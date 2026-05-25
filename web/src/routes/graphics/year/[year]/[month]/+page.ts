import { error } from '@sveltejs/kit';
import { loadGraphics, monthLabel } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}
	if (!/^\d{2}$/.test(params.month)) {
		error(404, 'invalid month');
	}

	const key = `${params.year}-${params.month}`;
	const cache = await loadGraphics(fetch);
	const graphics = cache.byYearMonth.get(key) ?? [];
	if (graphics.length === 0) {
		error(404, `no graphics for ${key}`);
	}

	return {
		year: yearNum,
		month: params.month,
		monthName: monthLabel(params.month),
		graphics
	};
};
