import { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';

// Animated walk-through of one closed-loop bloating tick. Auto-plays through the
// five stages; each stage lights up the active node(s) + the connector carrying
// data, and explains the interaction + its wire payload. Click a node or a stage
// pill to drive it yourself.

const C = { accent: '#ff9900', blue: '#00b3ff', green: '#34d399', dim: '#7ba6c0' };

type NodeId = 'controller' | 'nm' | 'disk' | 'sidecar';

interface Node {
  id: NodeId;
  label: string;
  sub: string;
  tag: string;
  color: string;
}

const NODES: Node[] = [
  { id: 'controller', label: 'Controller', sub: 'Go orchestrator', tag: 'verb scoring · projected gradient', color: C.accent },
  { id: 'nm', label: 'Nethermind', sub: 'engine + StateDiffsWriter', tag: 'testing_commitBlockV1', color: C.blue },
  { id: 'disk', label: 'RocksDB', sub: 'BlockDiffs column family', tag: 'compact RLP per block', color: C.dim },
  { id: 'sidecar', label: 'Sidecar', sub: 'Go · RocksDB secondary', tag: 'statecomp_get + Prometheus', color: C.green },
];

type EdgeId = 'e1' | 'e2' | 'e3';

interface Step {
  name: string;
  actor: NodeId;
  to?: NodeId;
  edge?: EdgeId;
  feedback?: boolean;
  color: string;
  desc: string;
  code: string;
}

const STEPS: Step[] = [
  {
    name: 'Decide',
    actor: 'controller',
    color: C.accent,
    desc: 'The controller compares the live composition to the mainnet-shaped target and scores the bloating “verbs”, picking the transaction mix that closes the gap fastest.',
    code: 'verb mix ← argmax( score | target − current )',
  },
  {
    name: 'Submit',
    actor: 'controller',
    to: 'nm',
    edge: 'e1',
    color: C.accent,
    desc: 'It assembles those transactions into a block and commits it through the Engine API — the same validated path a real consensus client would drive.',
    code: 'engine_newPayloadV4 + forkchoiceUpdated  ·  testing_commitBlockV1',
  },
  {
    name: 'Persist',
    actor: 'nm',
    to: 'disk',
    edge: 'e2',
    color: C.blue,
    desc: 'Nethermind executes the block; the StateDiffsWriter plugin walks the trie diff it already computed and writes one compact RLP record to the BlockDiffs column family — and nothing else. The block is also appended to payloads.rlp for bit-exact replay.',
    code: '[ blockNumber, postStateRoot, codeHashChanges[], slotCountChanges[], … ]',
  },
  {
    name: 'Tail',
    actor: 'disk',
    to: 'sidecar',
    edge: 'e3',
    color: C.green,
    desc: 'The sidecar opens the very same RocksDB in secondary mode and tails the BlockDiffs CF. No engine memory is touched and there is no rescan on restart — it can be killed and restarted independently of the chain.',
    code: 'OpenAsSecondary → TryCatchUpWithPrimary( BlockDiffs )',
  },
  {
    name: 'Feedback',
    actor: 'sidecar',
    to: 'controller',
    feedback: true,
    color: C.green,
    desc: 'It folds each diff into the running state composition and serves it over statecomp_get. The controller reads it on the next tick — closing the loop without ever blocking the engine.',
    code: 'statecomp_get → { accounts, storage, code, slots, bytes }',
  },
];

const EDGE_INDEX: Record<EdgeId, number> = { e1: 0, e2: 1, e3: 2 };

function Dot({ color, active, reverse = false, delay }: { color: string; active: boolean; reverse?: boolean; delay: number }) {
  const from = reverse ? '100%' : '0%';
  const to = reverse ? '0%' : '100%';
  return (
    <motion.span
      className="absolute top-1/2 h-2 w-2 rounded-full"
      style={{ marginTop: -4, background: color, boxShadow: `0 0 8px ${color}` }}
      initial={{ left: from, opacity: 0 }}
      animate={{ left: [from, to], opacity: active ? [0, 1, 1, 0] : [0, 0.45, 0.45, 0] }}
      transition={{ duration: active ? 1.1 : 2.6, repeat: Infinity, ease: 'linear', delay }}
    />
  );
}

function Connector({ color, active, vertical }: { color: string; active: boolean; vertical: boolean }) {
  if (vertical) {
    return (
      <div className="relative h-10 w-full flex items-center justify-center" aria-hidden="true">
        <div className="absolute h-full w-px" style={{ background: active ? color : '#1e3a52' }} />
        <span className="absolute bottom-0 text-sm" style={{ color: active ? color : C.dim }}>▾</span>
      </div>
    );
  }
  return (
    <div className="relative flex-1 self-center h-6 mx-1 min-w-[40px]" aria-hidden="true">
      <div
        className="absolute top-1/2 left-0 right-2 h-[2px] -translate-y-1/2 transition-colors duration-500"
        style={{ background: active ? color : '#1e3a52' }}
      />
      <span className="absolute top-1/2 right-0 -translate-y-1/2 text-sm transition-colors duration-500" style={{ color: active ? color : C.dim }}>▸</span>
      <Dot color={color} active={active} delay={0} />
      <Dot color={color} active={active} delay={active ? 0.45 : 1.0} />
    </div>
  );
}

export default function ArchitectureFlow() {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const interacted = useRef(false);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setStep((s) => (s + 1) % STEPS.length), 3200);
    return () => clearInterval(id);
  }, [playing]);

  const cur = STEPS[step]!;
  const activeNodes = new Set<NodeId>([cur.actor, ...(cur.to ? [cur.to] : [])]);
  const activeEdge = cur.edge;
  const feedbackActive = !!cur.feedback;

  const pick = (i: number) => {
    interacted.current = true;
    setPlaying(false);
    setStep(i);
  };
  const jumpToNode = (id: NodeId) => {
    const i = STEPS.findIndex((s) => s.actor === id);
    if (i >= 0) pick(i);
  };

  return (
    <div className="not-prose my-8 rounded-2xl border border-ink-800 bg-ink-900/40 p-5 sm:p-6">
      {/* stage pills + transport */}
      <div className="flex items-center justify-between gap-3 flex-wrap mb-5">
        <div className="flex flex-wrap gap-1.5">
          {STEPS.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => pick(i)}
              className={[
                'rounded-full px-3 py-1 text-xs font-mono border transition-colors',
                i === step ? 'text-white' : 'border-ink-700 text-ink-400 hover:text-white hover:border-ink-500',
              ].join(' ')}
              style={i === step ? { borderColor: s.color, background: `${s.color}1f` } : undefined}
              aria-pressed={i === step}
            >
              {i + 1}. {s.name}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => { interacted.current = true; setPlaying((p) => !p); }}
          className="rounded-md border border-ink-700 px-2.5 py-1 text-xs font-mono text-ink-300 hover:text-white hover:border-ink-500 transition-colors"
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? '❚❚ pause' : '▶ play'}
        </button>
      </div>

      {/* diagram — horizontal on sm+, stacked on mobile */}
      <div className="flex flex-col sm:flex-row sm:items-stretch">
        {NODES.map((n, i) => {
          const on = activeNodes.has(n.id);
          return (
            <div key={n.id} className="contents">
              <motion.button
                type="button"
                onClick={() => jumpToNode(n.id)}
                className="flex-1 rounded-xl border p-3 sm:p-4 text-center min-w-0 cursor-pointer"
                animate={{
                  borderColor: on ? n.color : '#1e3a52',
                  backgroundColor: on ? `${n.color}14` : 'rgba(0,26,44,0.4)',
                  boxShadow: on ? `0 0 22px ${n.color}33` : '0 0 0px rgba(0,0,0,0)',
                  scale: on ? 1.03 : 1,
                }}
                transition={{ duration: 0.4 }}
              >
                <div className="font-display text-white text-sm sm:text-base">{n.label}</div>
                <div className="text-[11px] text-ink-400 mt-0.5">{n.sub}</div>
                <div className="mt-2 text-[10px] font-mono leading-tight" style={{ color: on ? n.color : C.dim }}>{n.tag}</div>
              </motion.button>

              {i < NODES.length - 1 && (
                <>
                  <div className="hidden sm:flex">
                    <Connector color={cur.color} active={activeEdge != null && EDGE_INDEX[activeEdge] === i} vertical={false} />
                  </div>
                  <div className="sm:hidden">
                    <Connector color={cur.color} active={activeEdge != null && EDGE_INDEX[activeEdge] === i} vertical />
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* payloads.rlp branch */}
      <div className="mt-2 flex justify-center sm:justify-start sm:pl-[26%]">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-mono text-ink-500">
          <span style={{ color: activeEdge === 'e2' ? C.blue : C.dim }}>└▸</span>
          payloads.rlp <span className="text-ink-600">— every block, for bit-exact replay</span>
        </span>
      </div>

      {/* feedback lane */}
      <div className="relative mt-3 h-9 rounded-lg border px-3 flex items-center transition-colors duration-500"
        style={{ borderColor: feedbackActive ? C.green : '#13405c', background: feedbackActive ? `${C.green}0f` : 'rgba(0,26,44,0.5)' }}>
        <span className="text-sm mr-2" style={{ color: feedbackActive ? C.green : C.dim }}>◂</span>
        <span className="text-[11px] sm:text-xs text-ink-300">
          <span className="font-mono" style={{ color: feedbackActive ? C.green : C.dim }}>feedback</span>
          {' '}— sidecar serves the live composition back to the controller (right → left), closing the loop
        </span>
        <div className="absolute inset-x-3 inset-y-0 pointer-events-none" aria-hidden="true">
          <Dot color={C.green} active={feedbackActive} reverse delay={0} />
          <Dot color={C.green} active={feedbackActive} reverse delay={feedbackActive ? 0.5 : 1.2} />
        </div>
      </div>

      {/* narration */}
      <div className="mt-5 rounded-xl border border-ink-800 bg-ink-950/50 p-4">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: cur.color }} />
          <span className="text-xs font-mono uppercase tracking-wider" style={{ color: cur.color }}>
            {step + 1} / {STEPS.length} · {cur.name}
          </span>
        </div>
        <p className="text-sm text-ink-200 leading-relaxed">{cur.desc}</p>
        <pre className="mt-3 overflow-x-auto rounded-md border border-ink-800 bg-ink-950 px-3 py-2 text-[11px] sm:text-xs font-mono text-ink-300"><code>{cur.code}</code></pre>
      </div>
    </div>
  );
}
