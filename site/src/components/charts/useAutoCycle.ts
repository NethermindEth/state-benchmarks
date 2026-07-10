import { useEffect, useRef, useState } from 'react';

// Auto-advance through N views until the reader takes over (same idiom as the
// hero / ScalingContrast). Returns the active index + a manual picker that
// freezes the auto-cycle.
export function useAutoCycle(count: number, intervalMs = 3200) {
  const [idx, setIdx] = useState(0);
  const interacted = useRef(false);

  useEffect(() => {
    if (count <= 1) return;
    const id = setInterval(() => {
      if (!interacted.current) setIdx((i) => (i + 1) % count);
    }, intervalMs);
    return () => clearInterval(id);
  }, [count, intervalMs]);

  const pick = (i: number) => {
    interacted.current = true;
    setIdx(i);
  };
  return { idx, pick };
}
