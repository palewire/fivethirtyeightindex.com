import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

// On GitHub Pages this lives at palewire.github.io/fakethirtyeight.com/
// so we set the base path accordingly. Local dev (vite dev) ignores it.
const dev = process.env.NODE_ENV !== 'production';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: '404.html',
			precompress: false,
			strict: true
		}),
		paths: {
			base: dev ? '' : '/fakethirtyeight.com'
		},
		prerender: {
			handleHttpError: 'warn',
			handleMissingId: 'warn',
			// Dynamic byline pages depend on the live data file. Warn rather
			// than fail if a route has no entries (smoke builds, fresh data).
			handleUnseenRoutes: 'warn'
		}
	}
};

export default config;
