import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import {
	buildDatasetCache,
	buildEntryCache,
	buildGraphicCache,
	buildPodcastCache,
	type DataCache,
	type DatasetCache,
	type GraphicCache,
	type PodcastCache
} from '$lib/data';
import type { Dataset, Entry, Graphic, Podcast } from '$lib/types';

async function readJson<T>(filename: string): Promise<T[]> {
	const path = resolve(process.cwd(), 'static/data', filename);
	const text = await readFile(path, 'utf8');
	return JSON.parse(text) as T[];
}

export async function loadEntriesFromDisk(): Promise<DataCache> {
	const all = await readJson<Entry>('articles.json');
	return buildEntryCache(all);
}

export async function loadDatasetsFromDisk(): Promise<DatasetCache> {
	const all = await readJson<Dataset>('datasets.json');
	return buildDatasetCache(all);
}

export async function loadPodcastsFromDisk(): Promise<PodcastCache> {
	const all = await readJson<Podcast>('podcasts.json');
	return buildPodcastCache(all);
}

export async function loadGraphicsFromDisk(): Promise<GraphicCache> {
	const all = await readJson<Graphic>('graphics.json');
	return buildGraphicCache(all);
}

export async function loadIllustrationsFromDisk(): Promise<GraphicCache> {
	const all = await readJson<Graphic>('illustrations.json');
	return buildGraphicCache(all, [
		{ slug: 'artistic-illustration', name: 'Illustrations', count: all.length }
	]);
}
