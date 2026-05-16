import { error } from '@sveltejs/kit';
import { loadEntries } from '$lib/data';
import type { PageLoad, EntryGenerator } from './$types';

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
	return { year: yearNum, entries };
};

export const entries: EntryGenerator = async () => {
	// SvelteKit prerender needs to know which dynamic routes exist.
	const cache = await loadEntries();
	return cache.years.map((y) => ({ year: String(y) }));
};
