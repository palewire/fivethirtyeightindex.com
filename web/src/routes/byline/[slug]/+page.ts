import { error } from '@sveltejs/kit';
import { loadEntries } from '$lib/data';
import type { PageLoad, EntryGenerator } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const cache = await loadEntries(fetch);
	const bucket = cache.byBylineSlug.get(params.slug);
	if (!bucket) {
		error(404, 'unknown byline');
	}
	return { slug: params.slug, name: bucket.name, entries: bucket.entries };
};

export const entries: EntryGenerator = async () => {
	const cache = await loadEntries();
	return [...cache.byBylineSlug.keys()].map((slug) => ({ slug }));
};
