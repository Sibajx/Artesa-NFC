import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { adminApi } from './api';

// Search of artisans and pieces from any page (menu, top). It uses the same list
// endpoints as Artesanos and Piezas, so it matches what those pages match:
// artisan name, artistic name and slug; piece name, slug and public code.
// Ctrl+K (or "/") focuses it; the results open below it, inside the menu.

interface Hit {
  key: string;
  group: 'Artesanos' | 'Piezas';
  label: string;
  detail: string;
  to: string;
}

const MIN_CHARS = 2;
const DEBOUNCE_MS = 250;
const MAX_PER_GROUP = 5;

export function GlobalSearch() {
  const navigate = useNavigate();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [active, setActive] = useState(0);

  const query = text.trim();
  const searching = query.length >= MIN_CHARS;

  useEffect(() => {
    if (!searching) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setState('loading');
      Promise.all([adminApi.artisans({ q: query }, controller.signal), adminApi.pieces({ q: query }, controller.signal)]).then(
        ([artisans, pieces]) => {
          setHits([
            ...artisans.data.slice(0, MAX_PER_GROUP).map((a): Hit => ({
              key: `a:${a.id}`, group: 'Artesanos', label: a.artistic_name || a.full_name,
              detail: a.artistic_name ? a.full_name : `${a.piece_count} pieza${a.piece_count === 1 ? '' : 's'}`, to: `/artesanos/${a.id}`,
            })),
            ...pieces.data.slice(0, MAX_PER_GROUP).map((p): Hit => ({
              key: `p:${p.id}`, group: 'Piezas', label: p.name, detail: p.public_code, to: `/piezas/${p.id}`,
            })),
          ]);
          setActive(0);
          setState('done');
        },
        () => {
          if (!controller.signal.aborted) setState('error');
        },
      );
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, searching]);

  // Ctrl+K or "/" (outside a field) to jump to the search.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const typing = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable);
      if ((e.key === 'k' && (e.ctrlKey || e.metaKey)) || (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey)) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function open(hit: Hit) {
    setText('');
    setHits([]);
    setState('idle');
    inputRef.current?.blur();
    navigate(hit.to);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      setText('');
      setState('idle');
      return;
    }
    if (!searching || hits.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % hits.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i - 1 + hits.length) % hits.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      open(hits[active]);
    }
  }

  const showList = searching && state === 'done' && hits.length > 0;
  const message = !searching ? null
    : state === 'loading' ? 'Buscando…'
      : state === 'error' ? 'No se pudo buscar. Intenta de nuevo.'
        : state === 'done' && hits.length === 0 ? 'Sin resultados.' : null;

  return (
    <div className="global-search px-3 pb-3">
      <label className="sr-only" htmlFor={`${listId}-input`}>Buscar artesanos y piezas</label>
      <input
        id={`${listId}-input`}
        ref={inputRef}
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${listId}-${active}` : undefined}
        autoComplete="off"
        maxLength={100}
        placeholder="Buscar… (Ctrl+K)"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (e.target.value.trim().length < MIN_CHARS) {
            setHits([]);
            setState('idle');
          }
        }}
        onKeyDown={onKeyDown}
        className="global-search__input"
      />
      <div aria-live="polite" className="global-search__message">{message}</div>
      {showList && (
        <ul id={listId} role="listbox" aria-label="Resultados" className="global-search__list">
          {hits.map((hit, i) => (
            <li
              key={hit.key}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); open(hit); }}
              onMouseEnter={() => setActive(i)}
              className={`global-search__item ${i === active ? 'is-active' : ''}`}
            >
              <span className="global-search__label">{hit.label}</span>
              <span className="global-search__detail">{hit.group === 'Piezas' ? 'Pieza' : 'Artesano'} · {hit.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
