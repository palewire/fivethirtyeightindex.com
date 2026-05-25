import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/thumb': {
				target: 'https://archive.org/services/img',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/thumb/, '')
			}
		}
	}
});
