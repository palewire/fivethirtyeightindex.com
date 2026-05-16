// Pre-render every route. adapter-static will emit pure HTML on build.
export const prerender = true;
// We're statically hosted, so disable SSR fallback rendering for dev.
export const trailingSlash = 'always';
