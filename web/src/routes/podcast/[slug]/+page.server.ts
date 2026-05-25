import { loadPodcastsFromDisk } from '$lib/server/data';
import type { EntryGenerator } from './$types';

export const entries: EntryGenerator = async () => {
	const cache = await loadPodcastsFromDisk();
	return [...cache.bySeriesSlug.keys()].map((slug) => ({ slug }));
};
