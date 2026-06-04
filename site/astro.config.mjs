import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import tailwind from '@astrojs/tailwind';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export default defineConfig({
  site: 'https://literate-adventure-92og99n.pages.github.io',
  base: '/',
  trailingSlash: 'ignore',
  integrations: [
    mdx(),
    tailwind({ applyBaseStyles: false }),
    react(),
    sitemap(),
  ],
  markdown: {
    remarkPlugins: [remarkMath],
    rehypePlugins: [rehypeKatex],
    shikiConfig: { theme: 'github-dark-dimmed' },
  },
});
