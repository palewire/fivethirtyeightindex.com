import { error } from '@sveltejs/kit';
import { monthLabel } from '$lib/data';
import { loadIllustrationsFromDisk } from '$lib/server/data';
import type { EntryGenerator, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const yearNum = Number(params.year);
	if (!Number.isInteger(yearNum)) {
		error(404, 'invalid year');
	}
	if (!/^\d{2}$/.test(params.month)) {
		error(404, 'invalid month');
	}

	const key = `${params.year}-${params.month}`;
	const cache = await loadIllustrationsFromDisk();
	const illustrations = cache.byYearMonth.get(key) ?? [];
	if (illustrations.length === 0) {
		error(404, `no illustrations for ${key}`);
	}

	return {
		year: yearNum,
		month: params.month,
		monthName: monthLabel(params.month),
		illustrations
	};
};

export const entries: EntryGenerator = async () => {
	const cache = await loadIllustrationsFromDisk();
	const out: { year: string; month: string }[] = [];
	for (const ym of cache.byYearMonth.keys()) {
		const [year, month] = ym.split('-');
		out.push({ year, month });
	}
	return out;
};
