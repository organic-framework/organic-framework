// The Organic Framework docs site: Starlight (starlight.astro.build), published to GitHub Pages by
// .github/workflows/docs.yml. The pages are the repository's own Markdown, copied in by scripts/sync.py, which
// also writes the sidebar this file reads. Settings come from the environment; the workflow sets them from
// actions/configure-pages, and the defaults are the organization organic-framework's Pages site.
import { readFileSync } from 'node:fs';
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

const site = process.env.OF_SITE || 'https://organic-framework.github.io';
const base = process.env.OF_SITE_BASE ?? '/organic-framework';
const repo = (process.env.OF_REPO_URL || 'https://github.com/organic-framework/organic-framework').replace(/\/$/, '');
const sidebar = JSON.parse(readFileSync(new URL('./src/generated/sidebar.json', import.meta.url), 'utf8'));

export default defineConfig({
	site,
	base: base === '' ? '/' : base,
	integrations: [
		starlight({
			title: 'Organic Framework',
			description: 'Grow an organization of people and agents. The state-of-the-art way to build, run and reshape a company, with the human experience at its heart.',
			logo: { light: './src/assets/of-mark-light.svg', dark: './src/assets/of-mark-dark.svg' },
			favicon: '/favicon.svg',
			social: [{ icon: 'github', label: 'GitHub', href: repo }],
			customCss: ['@fontsource-variable/inter-tight', '@fontsource-variable/jetbrains-mono', './src/styles/of.css'],
			lastUpdated: false,
			sidebar,
		}),
	],
});
