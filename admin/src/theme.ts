// Claro / oscuro / auto (sigue al sistema). La elección se guarda en el
// navegador; sin elección, manda prefers-color-scheme (ver index.css).
export type ThemeChoice = 'light' | 'dark' | 'system';

const KEY = 'artesa-theme';

export function readTheme(): ThemeChoice {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    // Sin almacenamiento (ventana privada): se queda en "auto".
  }
  return 'system';
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', choice);
  try {
    if (choice === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // La elección vale para esta sesión aunque no se pueda guardar.
  }
}
