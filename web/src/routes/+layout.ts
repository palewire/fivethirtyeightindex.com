import { base } from '$app/paths';
import type { LayoutLoad } from './$types';

// Pre-render every route. adapter-static will emit pure HTML on build.
export const prerender = true;
export const trailingSlash = 'always';

// Layout data: just the total entry count for the tagline. We load it from
// a tiny dedicated meta file (~25 bytes) instead of the full articles.json
// (~8 MB) so every page renders fast.
export const load: LayoutLoad = async ({ fetch }) => {
	const resp = await fetch(`${base}/data/articles-meta.json`);
	const meta = (await resp.json()) as { total: number };
	return { total: meta.total };
};
