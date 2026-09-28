// One request lifecycle for an island: loading → result, with retry. Late
// answers from a superseded request are ignored. The loader is captured once
// (islands derive it from the URL, which does not change without navigation).
import { useCallback, useEffect, useState } from "react";

export type Loadable<R> = { readonly kind: "loading" } | R;

export function useApi<R>(load: () => Promise<R>): { state: Loadable<R>; retry: () => void } {
  const [loader] = useState(() => load);
  const [state, setState] = useState<Loadable<R>>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    void loader().then((result) => {
      if (current) setState(result);
    });
    return () => {
      current = false;
    };
  }, [loader, attempt]);

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);
  return { state, retry };
}
