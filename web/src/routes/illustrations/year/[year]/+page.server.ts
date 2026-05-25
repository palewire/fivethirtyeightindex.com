import { loadIllustrationsFromDisk } from '$lib/server/data';
import type { EntryGenerator } from './$types';

export const entries: EntryGenerator = async () => {
	const cache = await loadIllustrationsFromDisk();
	return cache.years.map((year) => ({ year: String(year) }));
};
