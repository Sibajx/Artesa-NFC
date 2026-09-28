import { useEffect, useRef, useState } from "react";

// True once the element has come near the viewport (never flips back).
export function useInView<T extends Element>(rootMargin = "200px") {
  const ref = useRef<T>(null);
  // Without IntersectionObserver everything counts as visible.
  const [inView, setInView] = useState(() => !("IntersectionObserver" in window));
  useEffect(() => {
    const el = ref.current;
    if (!el || inView) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [inView, rootMargin]);
  return { ref, inView };
}
