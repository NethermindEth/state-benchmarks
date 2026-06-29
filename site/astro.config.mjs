import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import tailwind from '@astrojs/tailwind';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

const BASE = '/state-benchmarks';

// Markdown/MDX links are authored as absolute site paths (e.g. /results) and are
// NOT rewritten with Astro's base. This walks the rendered tree and prefixes the
// base onto internal absolute href/src — leaving external (//, http) and already-
// prefixed links untouched.
function rehypeBasePrefix() {
  const walk = (node) => {
    if (node.type === 'element' && node.properties) {
      for (const attr of ['href', 'src']) {
        const v = node.properties[attr];
        if (
          typeof v === 'string' &&
          v.startsWith('/') &&
          !v.startsWith('//') &&
          v !== BASE &&
          !v.startsWith(BASE + '/')
        ) {
          node.properties[attr] = BASE + v;
        }
      }
    }
    if (node.children) node.children.forEach(walk);
  };
  return (tree) => walk(tree);
}

export default defineConfig({
  site: 'https://nethermindeth.github.io',
  base: BASE,
  trailingSlash: 'ignore',
  integrations: [
    mdx(),
    tailwind({ applyBaseStyles: false }),
    react(),
    sitemap(),
  ],
  markdown: {
    remarkPlugins: [remarkMath],
    rehypePlugins: [rehypeKatex, rehypeBasePrefix],
    shikiConfig: { theme: 'github-dark-dimmed' },
  },
});
