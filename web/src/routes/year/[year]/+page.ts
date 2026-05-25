import { error } from '@sveltejs/kit';
import { loadEntries } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}
	const cache = await loadEntries(fetch);
	const entries = cache.byYear.get(yearNum) ?? [];
	if (entries.length === 0) {
		error(404, `no entries for ${yearNum}`);
	}
	const months = cache.monthsByYear.get(yearNum) ?? [];
	return { year: yearNum, entries, months };
};
