import { useEffect, useRef } from 'react';
import { animate, useInView, useMotionValue, useTransform, motion } from 'motion/react';

type Fmt = 'int' | 'dp1' | 'dp2';
export interface Insight {
  to?: number;       // numeric target → counts up on scroll-in; omit for static text
  text?: string;     // static value (e.g. '✓ 1×–5×', 'log₁₆(N)', '8 → 9')
  fmt?: Fmt;
  prefix?: string;   // '+', '×', '−'
  suffix?: string;   // ' lvl', '%', 'M', ' KB'
  label: string;     // short caption under the value
  color?: string;    // hex accent
  bar?: number;      // 0..1 micro-bar fill (optional)
}
interface Props { items: Insight[] }

const ACCENT = '#4fc9ff';
const fmtNum = (v: number, f: Fmt = 'int') =>
  f === 'dp2' ? v.toFixed(2) : f === 'dp1' ? v.toFixed(1) : Math.round(v).toLocaleString();

function Chip({ it }: { it: Insight }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: '-40px' });
  const n = useMotionValue(0);
  const b = useMotionValue(0);
  const valTxt = useTransform(n, (v) => `${it.prefix ?? ''}${fmtNum(v, it.fmt)}${it.suffix ?? ''}`);
  const barW = useTransform(b, (v) => `${v * 100}%`);

  useEffect(() => {
    if (!inView) return;
    const cs = [];
    if (it.to != null) cs.push(animate(n, it.to, { duration: 1.1, ease: 'easeOut' }));
    if (it.bar != null) cs.push(animate(b, it.bar, { duration: 1.1, ease: 'easeOut' }));
    return () => cs.forEach((c) => c.stop());
  }, [inView]);

  const color = it.color ?? ACCENT;
  return (
    <div
      ref={ref}
      className="flex-1 min-w-[9rem] rounded-xl border border-ink-800 bg-ink-900/50 px-4 py-3"
    >
      <div className="font-display text-2xl tabular-nums leading-tight" style={{ color }}>
        {it.text != null ? it.text : <motion.span>{valTxt}</motion.span>}
      </div>
      {it.bar != null && (
        <div className="mt-2 h-1 rounded-full bg-ink-800 overflow-hidden">
          <motion.div className="h-full rounded-full" style={{ width: barW, background: color }} />
        </div>
      )}
      <div className="mt-1.5 text-xs text-ink-400 leading-snug">{it.label}</div>
    </div>
  );
}

export default function ChartTakeaways({ items }: Props) {
  return (
    <div className="flex flex-wrap gap-3 mt-5">
      {items.map((it, i) => (
        <Chip key={i} it={it} />
      ))}
    </div>
  );
}
