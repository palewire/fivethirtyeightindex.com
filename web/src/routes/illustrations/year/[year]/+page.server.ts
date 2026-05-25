import { error } from '@sveltejs/kit';
import { loadIllustrationsFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}

	const cache = await loadIllustrationsFromDisk();
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

export const entries: EntryGenerator = async () => {
	const cache = await loadIllustrationsFromDisk();
	return cache.years.map((year) => ({ year: String(year) }));
};
