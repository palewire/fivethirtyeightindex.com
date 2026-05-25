import { base } from '$app/paths';
import type { LayoutLoad } from './$types';

// Pre-render every route. adapter-static will emit pure HTML on build.
export const prerender = true;
export const trailingSlash = 'always';

interface MetaTotal {
	total: number;
}

async function loadMetaTotal(fetcher: typeof fetch, path: string): Promise<number> {
	const resp = await fetcher(`${base}/data/${path}`);
	if (!resp.ok) return 0;
	const meta = (await resp.json()) as MetaTotal;
	return meta.total;
}

// Layout data: just the total archive-item count for the tagline. We load it
// from tiny dedicated meta files instead of full JSON payloads so every page
// renders fast.
export const load: LayoutLoad = async ({ fetch }) => {
	const totals = await Promise.all([
		loadMetaTotal(fetch, 'articles-meta.json'),
		loadMetaTotal(fetch, 'datasets-meta.json'),
		loadMetaTotal(fetch, 'podcasts-meta.json'),
		loadMetaTotal(fetch, 'graphics-meta.json'),
		loadMetaTotal(fetch, 'illustrations-meta.json')
	]);
	return { total: totals.reduce((sum, total) => sum + total, 0) };
};
