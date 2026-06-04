/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,ts,tsx,md,mdx}'],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9',
          100: '#eceef2',
          200: '#d4d9e2',
          300: '#aab2c2',
          400: '#7a839a',
          500: '#525c75',
          600: '#3b445a',
          700: '#2b3247',
          800: '#1b2032',
          900: '#0f1322',
          950: '#070a16',
        },
        accent: {
          400: '#7dd3fc',
          500: '#38bdf8',
          600: '#0ea5e9',
        },
        signal: {
          warn: '#fbbf24',
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
          'radial-gradient(circle at 1px 1px, rgba(125,211,252,0.18) 1px, transparent 0)',
      },
    },
  },
  plugins: [],
};
