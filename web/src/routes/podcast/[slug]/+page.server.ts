import { error } from '@sveltejs/kit';
import { loadPodcastsFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const cache = await loadPodcastsFromDisk();
	const bucket = cache.bySeriesSlug.get(params.slug);
	if (!bucket) {
		error(404, 'unknown podcast series');
	}
	return { slug: params.slug, name: bucket.name, podcasts: bucket.podcasts };
};

export const entries: EntryGenerator = async () => {
	const cache = await loadPodcastsFromDisk();
	return [...cache.bySeriesSlug.keys()].map((slug) => ({ slug }));
};
