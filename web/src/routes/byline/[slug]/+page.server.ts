import { error } from '@sveltejs/kit';
import { loadEntriesFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const cache = await loadEntriesFromDisk();
	const bucket = cache.byBylineSlug.get(params.slug);
	if (!bucket) {
		error(404, 'unknown byline');
	}
	return { slug: params.slug, name: bucket.name, entries: bucket.entries };
};

export const entries: EntryGenerator = async () => {
	const cache = await loadEntriesFromDisk();
	return [...cache.byBylineSlug.keys()].map((slug) => ({ slug }));
};
