// Tells the site header (scripts/site.ts) that a dark hero just rendered, so
// it switches to its light-on-dark tone while that hero is under it.
import { useEffect } from "react";

export function useDarkZone(active: boolean): void {
  useEffect(() => {
    if (active) window.dispatchEvent(new Event("artesa:dark-zone"));
  }, [active]);
}
