/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,ts,tsx,md,mdx}'],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        // Nethermind Dark Navy (#00253D) ramp — surfaces & text on a navy-black canvas
        ink: {
          50: '#eef5fa',
          100: '#d8e8f2',
          200: '#a9c8db',
          300: '#7ba6c0',
          400: '#4d7d9c',
          500: '#2f5d7c',
          600: '#13405c',
          700: '#003049',
          800: '#00253d', // Nethermind Dark Navy
          900: '#001a2c',
          950: '#00111d',
        },
        // Nethermind Blue (#00B3FF) — primary accent
        accent: {
          400: '#4fc9ff',
          500: '#00b3ff',
          600: '#0090d6',
        },
        // Nethermind Orange (#FF9900) — secondary accent
        brand: {
          navy: '#00253d',
          blue: '#00b3ff',
          orange: '#ff9900',
          white: '#ffffff',
        },
        signal: {
          warn: '#ff9900',
          danger: '#f87171',
          good: '#34d399',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        display: ['"Space Grotesk"', 'Inter', 'ui-sans-serif', 'sans-serif'],
      },
      backgroundImage: {
        'hero-grid':
          'radial-gradient(circle at 1px 1px, rgba(0,179,255,0.16) 1px, transparent 0)',
      },
    },
  },
  plugins: [],
};
