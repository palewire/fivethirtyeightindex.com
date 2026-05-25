import { loadEntriesFromDisk } from '$lib/server/data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const cache = await loadEntriesFromDisk();

	const all = [...cache.byBylineSlug.entries()].map(([slug, { name, entries }]) => ({
		slug,
		name,
		count: entries.length
	}));

	all.sort((a, b) => a.name.localeCompare(b.name));

	return {
		bylines: all,
		total: all.length
	};
};
