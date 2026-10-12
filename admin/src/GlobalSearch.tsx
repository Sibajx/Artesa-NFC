import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { adminApi } from './api';
import type { ArtisanSummary, PieceSummary } from './api';
import { usePermissions } from './roles-context';

// Search of artisans, pieces, supplies and training manuals from any page. Artisans
// and pieces use the same list endpoints as their pages, so it matches what those
// match (artisan name, artistic name and slug; piece name, slug and public code).
// Supplies and manuals are short lists filtered here by name; manuals only for
// accounts with the Training permission. With the box empty it offers what was
// updated last. Ctrl+K (or "/") focuses it; results open below it, inside the menu.

interface Hit {
  key: string;
  kind: 'Artesano' | 'Pieza' | 'Insumo' | 'Manual';
  label: string;
  detail: string;
  to: string;
}

const MIN_CHARS = 2;
const DEBOUNCE_MS = 250;
const MAX_PER_GROUP = 5;
const MAX_RECENT = 3;

const has = (haystack: string, needle: string) => haystack.toLowerCase().includes(needle.toLowerCase());

const artisanHit = (a: ArtisanSummary): Hit => ({
  key: `a:${a.id}`, kind: 'Artesano', label: a.artistic_name || a.full_name,
  detail: a.artistic_name ? a.full_name : `${a.piece_count} pieza${a.piece_count === 1 ? '' : 's'}`, to: `/artesanos/${a.id}`,
});

const pieceHit = (p: PieceSummary): Hit => ({ key: `p:${p.id}`, kind: 'Pieza', label: p.name, detail: p.public_code, to: `/piezas/${p.id}` });

export function GlobalSearch() {
  const navigate = useNavigate();
  const canTrain = usePermissions().includes('training');
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<Hit[] | null>(null);
  const [focused, setFocused] = useState(false);

  const query = text.trim();
  const searching = query.length >= MIN_CHARS;

  useEffect(() => {
    if (!searching) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setState('loading');
      Promise.all([
        adminApi.artisans({ q: query }, controller.signal),
        adminApi.pieces({ q: query }, controller.signal),
        adminApi.supplies(false, controller.signal).catch(() => null),
        canTrain ? adminApi.manuals(controller.signal).catch(() => null) : Promise.resolve(null),
      ]).then(
        ([artisans, pieces, supplies, manuals]) => {
          setHits([
            ...artisans.data.slice(0, MAX_PER_GROUP).map(artisanHit),
            ...pieces.data.slice(0, MAX_PER_GROUP).map(pieceHit),
            ...(supplies?.data ?? []).filter((s) => has(s.name, query)).slice(0, MAX_PER_GROUP).map((s): Hit => ({
              key: `s:${s.id}`, kind: 'Insumo', label: s.name, detail: `${s.unit} · stock ${s.stock}`, to: '/insumos',
            })),
            ...(manuals?.data ?? []).filter((m) => has(m.title, query) || has(m.craft, query)).slice(0, MAX_PER_GROUP).map((m): Hit => ({
              key: `m:${m.id}`, kind: 'Manual', label: m.title, detail: m.craft, to: '/capacitaciones',
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
  }, [query, searching, canTrain]);

  // Empty box + focus: the last things edited (read again on every focus, so a record
  // just created or edited shows up), so there is something to open at once.
  useEffect(() => {
    if (!focused || searching) return;
    const controller = new AbortController();
    Promise.all([adminApi.artisans({}, controller.signal), adminApi.pieces({}, controller.signal)]).then(
      ([artisans, pieces]) => {
        const latest = <T extends { updated_at: string }>(rows: T[]) =>
          [...rows].sort((x, y) => y.updated_at.localeCompare(x.updated_at)).slice(0, MAX_RECENT);
        setRecent([...latest(artisans.data).map(artisanHit), ...latest(pieces.data).map(pieceHit)]);
      },
      () => undefined,
    );
    return () => controller.abort();
  }, [focused, searching]);

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
    if (shown.length === 0 || (searching && state !== 'done')) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % shown.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i - 1 + shown.length) % shown.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      open(shown[active] ?? shown[0]);
    }
  }

  const shown = searching ? hits : recent ?? [];
  const showList = searching ? state === 'done' && hits.length > 0 : focused && shown.length > 0;
  const message = !searching ? null
    : state === 'loading' ? 'Buscando…'
      : state === 'error' ? 'No se pudo buscar. Intenta de nuevo.'
        : state === 'done' && hits.length === 0 ? 'Sin resultados.' : null;

  return (
    <div className="global-search px-3 pb-3">
      <label className="sr-only" htmlFor={`${listId}-input`}>Buscar artesanos, piezas, insumos y manuales</label>
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
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        className="global-search__input"
      />
      <div aria-live="polite" className="global-search__message">{message}</div>
      {showList && (
        <ul id={listId} role="listbox" aria-label="Resultados" className="global-search__list">
          {!searching && <li role="presentation" className="global-search__heading">Actualizado hace poco</li>}
          {shown.map((hit, i) => (
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
              <span className="global-search__detail">{hit.kind} · {hit.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
