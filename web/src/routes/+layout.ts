import { loadEntries } from '$lib/data';
import type { LayoutLoad } from './$types';

// Pre-render every route. adapter-static will emit pure HTML on build.
export const prerender = true;
export const trailingSlash = 'always';

// Layout data: the total entry count is shared by every page through the
// tagline, so we load it once here. `loadEntries` memoizes the JSON read.
export const load: LayoutLoad = async ({ fetch }) => {
	const cache = await loadEntries(fetch);
	return { total: cache.all.length };
};
