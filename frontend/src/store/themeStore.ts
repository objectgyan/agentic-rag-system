import { create } from 'zustand';

interface ThemeState {
  dark: boolean;
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
  toggle: () =>
    set((state) => {
      const next = !state.dark;
      document.documentElement.classList.toggle('dark', next);
      return { dark: next };
    }),
}));

// Initialize on load
if (useThemeStore.getState().dark) {
  document.documentElement.classList.add('dark');
}
