import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

// Base path detection:
//   • dev or custom-domain (CNAME present in static/): base = ''
//   • gh-pages subpath fallback: base = '/fakethirtyeight.com'
const dev = process.env.NODE_ENV !== 'production';
const hasCname = existsSync(resolve(process.cwd(), 'static/CNAME'));
const base = dev || hasCname ? '' : '/fakethirtyeight.com';

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
		paths: { base },
		prerender: {
			handleHttpError: 'warn',
			handleMissingId: 'warn',
			handleUnseenRoutes: 'warn'
		}
	}
};

export default config;
