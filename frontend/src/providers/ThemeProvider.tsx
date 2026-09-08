import * as React from "react";

export type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

const THEME_KEY = "radal.theme";

const ThemeContext = React.createContext<ThemeContextValue | undefined>(
  undefined,
);

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "light"; // default light
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>(getInitialTheme);
  const firstRun = React.useRef(true);

  React.useEffect(() => {
    const root = document.documentElement;

    /* A theme flip changes color, background, border and shadow on nearly
       every element at once — every transition on those properties fires
       together and the switch smears instead of snapping. Suppress ALL
       transitions, flip, force a reflow so styles land while they are off,
       then restore on the next frame (better-ui: suppress transitions on
       theme switch). Skipped on first mount — nothing changes there. */
    const suppress = !firstRun.current;
    firstRun.current = false;
    let style: HTMLStyleElement | null = null;
    if (suppress) {
      style = document.createElement("style");
      style.appendChild(
        document.createTextNode(
          "*,*::before,*::after{transition:none !important}",
        ),
      );
      document.head.appendChild(style);
    }

    root.classList.toggle("dark", theme === "dark");
    root.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);

    if (style) {
      // Recalculate styles while transitions are disabled, then re-enable.
      void root.offsetHeight;
      const el = style;
      requestAnimationFrame(() => {
        el.remove();
      });
    }
  }, [theme]);

  const setTheme = React.useCallback((t: Theme) => setThemeState(t), []);
  const toggleTheme = React.useCallback(
    () => setThemeState((prev) => (prev === "dark" ? "light" : "dark")),
    [],
  );

  const value = React.useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
