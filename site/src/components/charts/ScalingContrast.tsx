import { useEffect, useRef, useState } from 'react';
import { motion, useMotionValue, useTransform, animate } from 'motion/react';

interface Milestone {
  mult: number;
  totalGB: number;
  accountDepth?: number;
}
interface ProofRow {
  mult: number;
  accountP50: number;
}
interface Props {
  milestones: Milestone[];
  proofs: ProofRow[];
}

// A shared multiplicative axis (× mainnet). State size races to ×10 while trie
// depth and per-proof witness barely leave ×1 — that gap *is* the log₁₆ law.
const AXIS_MAX = 10.6;
const TICKS = [1, 2, 3, 5, 7, 10];
const COLORS = { state: '#00b3ff', depth: '#ff9900', witness: '#34d399' };

const fmtSize = (gb: number) => (gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${Math.round(gb)} GB`);
const widthPct = (r: number) => `${Math.min(100, (r / AXIS_MAX) * 100)}%`;

export default function ScalingContrast({ milestones, proofs }: Props) {
  const ms = milestones.filter((m) => m.totalGB != null && m.accountDepth != null);
  const proofByMult = new Map(proofs.map((p) => [p.mult, p.accountP50]));
  const base = {
    gb: ms[0]!.totalGB,
    depth: ms[0]!.accountDepth!,
    proof: proofByMult.get(ms[0]!.mult) ?? 3820,
  };

  const [idx, setIdx] = useState(0);
  const cur = ms[idx]!;

  // ratios vs mainnet (drive bar widths) + absolute readouts (drive counters)
  const stateR = useMotionValue(1);
  const depthR = useMotionValue(1);
  const witR = useMotionValue(1);
  const stateAbs = useMotionValue(base.gb);
  const depthAbs = useMotionValue(base.depth);
  const witAbs = useMotionValue(base.proof);

  const stateW = useTransform(stateR, widthPct);
  const depthW = useTransform(depthR, widthPct);
  const witW = useTransform(witR, widthPct);

  const mult = (r: number) => `×${r.toFixed(2)}`;
  const stateTxt = useTransform(stateAbs, fmtSize);
  const stateMult = useTransform(stateR, mult);
  const depthTxt = useTransform(depthAbs, (v) => `${v.toFixed(2)} lvl`);
  const depthMult = useTransform(depthR, mult);
  const witTxt = useTransform(witAbs, (v) => `${Math.round(v).toLocaleString()} B`);
  const witMult = useTransform(witR, mult);

  useEffect(() => {
    const proof = proofByMult.get(cur.mult) ?? base.proof;
    const opt = { duration: 1.0, ease: 'easeOut' as const };
    const c = [
      animate(stateR, cur.totalGB / base.gb, opt),
      animate(depthR, cur.accountDepth! / base.depth, opt),
      animate(witR, proof / base.proof, opt),
      animate(stateAbs, cur.totalGB, opt),
      animate(depthAbs, cur.accountDepth!, opt),
      animate(witAbs, proof, opt),
    ];
    return () => c.forEach((x) => x.stop());
  }, [idx]);

  // auto-advance through milestones until the reader takes over (same idiom as the hero)
  const interacted = useRef(false);
  useEffect(() => {
    const id = setInterval(() => {
      if (interacted.current) return;
      setIdx((i) => (i + 1) % ms.length);
    }, 2400);
    return () => clearInterval(id);
  }, []);
  const pick = (i: number) => {
    interacted.current = true;
    setIdx(i);
  };

  const rows = [
    { key: 'state', label: 'State size', color: COLORS.state, w: stateW, txt: stateTxt, m: stateMult },
    { key: 'depth', label: 'Trie depth', color: COLORS.depth, w: depthW, txt: depthTxt, m: depthMult },
    { key: 'witness', label: 'Witness / proof', color: COLORS.witness, w: witW, txt: witTxt, m: witMult },
  ];

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-6">
        {ms.map((m, i) => (
          <button
            key={m.mult}
            type="button"
            onClick={() => pick(i)}
            className={[
              'rounded-full px-3 py-1 text-sm font-mono border transition-colors',
              i === idx
                ? 'border-accent-500 bg-accent-500/15 text-white'
                : 'border-ink-700 text-ink-300 hover:text-white hover:border-ink-500',
            ].join(' ')}
            aria-pressed={i === idx}
          >
            {m.mult}×
          </button>
        ))}
      </div>

      <div className="relative pb-6">
        {/* shared × mainnet gridlines */}
        <div className="absolute inset-x-0 top-0 bottom-6 pointer-events-none" aria-hidden="true">
          {TICKS.map((t) => (
            <div
              key={t}
              className="absolute top-0 bottom-0 border-l border-ink-800/70"
              style={{ left: `${(t / AXIS_MAX) * 100}%` }}
            >
              <span className="absolute -bottom-5 -translate-x-1/2 text-[10px] font-mono text-ink-500">{t}×</span>
            </div>
          ))}
        </div>

        <div className="space-y-4">
          {rows.map((r) => (
            <div key={r.key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs uppercase tracking-wider text-ink-400">{r.label}</span>
                <span className="font-mono text-sm tabular-nums">
                  <motion.span className="text-ink-100">{r.txt}</motion.span>
                  <motion.span className="text-ink-500 ml-2">{r.m}</motion.span>
                </span>
              </div>
              <div className="h-7 rounded-md bg-ink-900/80 border border-ink-800 overflow-hidden">
                <motion.div
                  className="h-full rounded-md"
                  style={{ width: r.w, background: `linear-gradient(90deg, ${r.color}33, ${r.color})` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      <p className="mt-3 text-sm text-ink-400 leading-relaxed">
        Same axis. At <span className="font-mono text-white">{cur.mult}×</span> the{' '}
        <span style={{ color: COLORS.state }}>state</span> is <span className="font-mono">{mult(cur.totalGB / base.gb)}</span> mainnet, but{' '}
        <span style={{ color: COLORS.depth }}>trie depth</span> and the{' '}
        <span style={{ color: COLORS.witness }}>per-proof witness</span> barely move — both grow like{' '}
        <span className="font-mono">log₁₆(N)</span>, so a 10× state adds only ~0.8 of a trie level and ~11% per proof.{' '}
        <span className="text-ink-200">That is why a 10× state is not a 10× cost.</span>
      </p>
    </div>
  );
}
