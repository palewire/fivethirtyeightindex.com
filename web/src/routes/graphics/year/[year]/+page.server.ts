import { error } from '@sveltejs/kit';
import { loadGraphicsFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}

	const cache = await loadGraphicsFromDisk();
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

export const entries: EntryGenerator = async () => {
	const cache = await loadGraphicsFromDisk();
	return cache.years.map((year) => ({ year: String(year) }));
};
