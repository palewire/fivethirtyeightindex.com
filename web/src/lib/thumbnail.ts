import { dev } from '$app/environment';

const THUMBNAIL_CACHE_ORIGIN = 'https://thumbs.fivethirtyeightindex.com';

export function thumbnailUrl(url: string): string {
	if (!dev) return url;
	return url.startsWith(`${THUMBNAIL_CACHE_ORIGIN}/`)
		? `/thumb${url.slice(THUMBNAIL_CACHE_ORIGIN.length)}`
		: url;
}
