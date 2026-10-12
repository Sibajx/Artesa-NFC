import { useId, useState } from 'react';
import type { KeyboardEvent } from 'react';

// Who was visited: search the registered artisans while typing, or just type a name
// that is not registered (a talk, a negotiation). Choosing one from the list links the
// visit to that artisan; anything else typed is kept as a plain name.

export interface Picked {
  artisanId: string; // '' when it is a typed name
  text: string;
}

interface Artisan {
  id: string;
  full_name: string;
}

const MAX_SHOWN = 6;

// Case- and accent-insensitive, so "jose" finds "José".
const plain = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().trim();

export function ArtisanPicker({ id: inputId, artisans, value, onChange, className }: {
  id: string; artisans: Artisan[]; value: Picked; onChange: (next: Picked) => void; className: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const query = plain(value.text);
  const matches = query ? artisans.filter((a) => plain(a.full_name).includes(query)).slice(0, MAX_SHOWN) : [];
  const showList = open && !value.artisanId && matches.length > 0;

  function pick(a: Artisan) {
    onChange({ artisanId: a.id, text: a.full_name });
    setOpen(false);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      setOpen(false);
      return;
    }
    if (!showList) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % matches.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i - 1 + matches.length) % matches.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pick(matches[active] ?? matches[0]);
    }
  }

  // Leaving the box with a name that equals a registered one links it, so a typed
  // "José Pérez" does not become a duplicate of the registered "José Pérez".
  function onBlur() {
    setOpen(false);
    if (value.artisanId || !query) return;
    const exact = artisans.filter((a) => plain(a.full_name) === query);
    if (exact.length === 1) onChange({ artisanId: exact[0].id, text: exact[0].full_name });
  }

  const status = value.artisanId ? 'Artesano registrado.' : value.text.trim() ? 'No está registrado: se guardará solo el nombre.' : null;

  return (
    <div>
      <input
        id={inputId}
        role="combobox"
        aria-expanded={showList}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${id}-${active}` : undefined}
        autoComplete="off"
        maxLength={200}
        value={value.text}
        placeholder="Escribe para buscar o poner un nombre nuevo"
        onChange={(e) => {
          onChange({ artisanId: '', text: e.target.value });
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={className}
      />
      {showList && (
        <ul id={`${id}-list`} role="listbox" aria-label="Artesanos registrados" className="mt-1 max-h-48 overflow-y-auto rounded-md border border-botanica-gris/30 bg-white p-1 shadow-sm">
          {matches.map((a, i) => (
            <li
              key={a.id}
              id={`${id}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); pick(a); }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer rounded px-2 py-1.5 text-sm text-botanica-negro ${i === active ? 'bg-botanica-hueso' : ''}`}
            >
              {a.full_name}
            </li>
          ))}
        </ul>
      )}
      <p aria-live="polite" className="mt-1 text-xs text-botanica-gris">{status}</p>
    </div>
  );
}
