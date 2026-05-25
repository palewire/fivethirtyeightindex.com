import { loadIllustrationsFromDisk } from '$lib/server/data';
import type { EntryGenerator } from './$types';

export const entries: EntryGenerator = async () => {
	const cache = await loadIllustrationsFromDisk();
	const out: { year: string; month: string }[] = [];
	for (const ym of cache.byYearMonth.keys()) {
		const [year, month] = ym.split('-');
		out.push({ year, month });
	}
	return out;
};
