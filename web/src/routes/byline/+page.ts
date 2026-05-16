import { loadEntries } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	const cache = await loadEntries(fetch);

	const all = [...cache.byBylineSlug.entries()].map(([slug, { name, entries }]) => ({
		slug,
		name,
		count: entries.length
	}));

	// Alphabetical by name. Group headers come from the first character.
	all.sort((a, b) => a.name.localeCompare(b.name));

	return {
		bylines: all,
		total: all.length
	};
};
