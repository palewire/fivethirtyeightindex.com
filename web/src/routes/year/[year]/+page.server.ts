import { error } from '@sveltejs/kit';
import { loadEntriesFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}
	const cache = await loadEntriesFromDisk();
	const entries = cache.byYear.get(yearNum) ?? [];
	if (entries.length === 0) {
		error(404, `no entries for ${yearNum}`);
	}
	const months = cache.monthsByYear.get(yearNum) ?? [];
	return { year: yearNum, entries, months };
};

export const entries: EntryGenerator = async () => {
	const cache = await loadEntriesFromDisk();
	return cache.years.map((year) => ({ year: String(year) }));
};
