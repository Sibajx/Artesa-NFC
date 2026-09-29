import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ApiError } from './api';

export type LoadState<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: ApiError };

// Loads once per change of `key`; cancels the previous request.
export function useLoad<T>(key: string, load: (signal: AbortSignal) => Promise<T>): LoadState<T> {
  const [state, setState] = useState<{ key: string; value: LoadState<T> }>({ key, value: { status: 'loading' } });

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => setState({ key, value: { status: 'ready', data } }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const apiError = error instanceof ApiError ? error : new ApiError('network', 0);
        setState({ key, value: { status: 'error', error: apiError } });
      },
    );
    return () => controller.abort();
    // `load` is recreated on every render; `key` identifies what it loads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state.key === key ? state.value : { status: 'loading' };
}

// List filters live in the URL, so a filtered view can be reloaded or shared.
export function useListFilters() {
  const [params, setParams] = useSearchParams();
  const status = params.get('estado') ?? '';
  const q = params.get('q') ?? '';

  const update = (key: 'estado' | 'q', value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return { status, q, setStatus: (v: string) => update('estado', v), setQ: (v: string) => update('q', v) };
}
