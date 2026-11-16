/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{html,ts}'],
  // Preflight aus: Angular Material bringt eigene Resets mit -> Konflikte vermeiden.
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        bg: '#0f1419',
        panel: '#1a222c',
        'panel-2': '#232e3a',
        border: '#2e3a47',
        muted: '#8b98a5',
        accent: '#3b82f6',
        ok: '#22c55e',
        warn: '#f59e0b',
        danger: '#ef4444',
      },
    },
  },
  plugins: [],
};
