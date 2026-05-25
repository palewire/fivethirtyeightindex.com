import { error } from '@sveltejs/kit';
import { loadPodcasts } from '$lib/data';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
	const cache = await loadPodcasts(fetch);
	const bucket = cache.bySeriesSlug.get(params.slug);
	if (!bucket) {
		error(404, 'unknown podcast series');
	}
	return { slug: params.slug, name: bucket.name, podcasts: bucket.podcasts };
};
