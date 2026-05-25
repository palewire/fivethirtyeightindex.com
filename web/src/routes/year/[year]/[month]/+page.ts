import { error } from '@sveltejs/kit';
import { loadEntries, monthLabel } from '$lib/data';
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
	const cache = await loadEntries(fetch);
	const entries = cache.byYearMonth.get(key) ?? [];
	if (entries.length === 0) {
		error(404, `no entries for ${key}`);
	}
	return {
		year: yearNum,
		month: params.month,
		monthName: monthLabel(params.month),
		entries
	};
};
