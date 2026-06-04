import { useEffect, useRef, useState } from 'react';
import { motion, useMotionValue, useTransform, animate } from 'motion/react';

interface Milestone {
  mult: number;
  accounts?: number;
  storage?: number;
  codeGB?: number;
  status: string;
  measured?: boolean;
  pending?: boolean;
}

interface Props {
  milestones: Milestone[];
}

const fmt = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}B` : `${Math.round(n)}M`;

export default function StateGrowthHero({ milestones }: Props) {
  const [idx, setIdx] = useState(0);
  const target = milestones[idx]!;
  const max = milestones[milestones.length - 1]!.mult;

  const accounts = useMotionValue(milestones[0]!.accounts ?? 0);
  const storage = useMotionValue(milestones[0]!.storage ?? 0);
  const code = useMotionValue(milestones[0]!.codeGB ?? 0);
  const radius = useMotionValue(60 + (milestones[0]!.mult / max) * 130);

  const aText = useTransform(accounts, (v) => fmt(v));
  const sText = useTransform(storage, (v) => fmt(v));
  const cText = useTransform(code, (v) => `${v.toFixed(1)} GB`);

  useEffect(() => {
    const t = target;
    const controls = [
      animate(radius, 60 + (t.mult / max) * 130, { duration: 0.9, ease: 'easeOut' }),
    ];
    // only animate the readouts for milestones that carry measured numbers
    if (!t.pending) {
      controls.push(
        animate(accounts, t.accounts ?? 0, { duration: 0.9, ease: 'easeOut' }),
        animate(storage, t.storage ?? 0, { duration: 0.9, ease: 'easeOut' }),
        animate(code, t.codeGB ?? 0, { duration: 0.9, ease: 'easeOut' }),
      );
    }
    return () => controls.forEach((c) => c.stop());
  }, [idx]);

  const userInteracted = useRef(false);
  useEffect(() => {
    if (userInteracted.current) return;
    const id = setInterval(() => {
      if (userInteracted.current) return;
      setIdx((i) => (i + 1) % milestones.length);
    }, 2200);
    return () => clearInterval(id);
  }, []);

  const onPick = (i: number) => {
    userInteracted.current = true;
    setIdx(i);
  };

  const stats = [
    { key: 'accounts', label: 'Accounts',      m: aText },
    { key: 'storage',  label: 'Storage slots', m: sText },
    { key: 'code',     label: 'Contract code', m: cText },
  ];

  return (
    <div className="relative grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
      <div className="relative aspect-square w-full max-w-md mx-auto">
        <svg viewBox="-200 -200 400 400" className="w-full h-full">
          <defs>
            <radialGradient id="blob" cx="0.5" cy="0.5" r="0.5">
              <stop offset="0%" stopColor="#7dd3fc" stopOpacity={0.95} />
              <stop offset="60%" stopColor="#0ea5e9" stopOpacity={0.55} />
              <stop offset="100%" stopColor="#0ea5e9" stopOpacity={0} />
            </radialGradient>
            <radialGradient id="ring" cx="0.5" cy="0.5" r="0.5">
              <stop offset="80%" stopColor="#38bdf8" stopOpacity={0} />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.6} />
            </radialGradient>
          </defs>
          <circle r={60} fill="none" stroke="#3b445a" strokeDasharray="4 4" strokeWidth={1} />
          <text x={0} y={-72} textAnchor="middle" fill="#7a839a" fontSize={12} fontFamily="JetBrains Mono">
            1x baseline
          </text>
          <circle r={60 + 130} fill="none" stroke="#1b2032" strokeWidth={1} />
          <text x={0} y={-(60 + 138)} textAnchor="middle" fill="#525c75" fontSize={12} fontFamily="JetBrains Mono">
            10x target
          </text>
          <motion.circle cx={0} cy={0} r={radius} fill="url(#blob)" initial={false} />
          <motion.circle cx={0} cy={0} r={radius} fill="url(#ring)" initial={false} />
          <text x={0} y={6} textAnchor="middle" fill="white" fontSize={34} fontFamily="Space Grotesk" fontWeight={700}>
            {target.mult}x
          </text>
        </svg>
      </div>

      <div className="space-y-6">
        <div className="flex items-center gap-2">
          {target.measured
            ? <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-signal-good/15 text-signal-good">measured</span>
            : <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-ink-800 text-ink-400">not generated yet</span>}
          <span className="text-xs text-ink-400">{target.mult}× mainnet</span>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {stats.map((stat) => (
            <div key={stat.key} className="rounded-xl border border-ink-800 bg-ink-900/60 p-4">
              <div className="text-xs uppercase tracking-wider text-ink-400 mb-1">{stat.label}</div>
              {target.pending
                ? <div className="font-display text-2xl text-ink-600 tabular-nums">—</div>
                : <motion.div className="font-display text-2xl text-white tabular-nums">{stat.m}</motion.div>}
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-2">
          {milestones.map((m, i) => (
            <button
              key={m.mult}
              type="button"
              onClick={() => onPick(i)}
              className={[
                'rounded-full px-3 py-1.5 text-sm font-mono border transition-colors',
                i === idx
                  ? 'border-accent-500 bg-accent-500/15 text-white'
                  : m.measured
                    ? 'border-ink-700 text-ink-300 hover:text-white hover:border-ink-500'
                    : 'border-ink-800 text-ink-500 hover:text-ink-300',
              ].join(' ')}
              aria-pressed={i === idx}
            >
              {m.mult}x
            </button>
          ))}
        </div>

        <p className="text-ink-300 max-w-md">
          As the state grows, every account touch traverses a deeper trie and emits a
          fatter witness. We benchmark the entire pipeline — execution, proof size,
          snap-sync — at each milestone so the protocol roadmap is grounded in evidence,
          not extrapolation.
        </p>
      </div>
    </div>
  );
}
