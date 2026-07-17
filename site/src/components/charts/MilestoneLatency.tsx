import { useAutoCycle } from './useAutoCycle';
import MilestonePills from './MilestonePills.tsx';
import EndpointLatencyChart from './EndpointLatencyChart.tsx';

interface Agg { nmP99: number; gethP99: number; nmMean: number; gethMean: number }
interface Milestone {
  mult: number;
  label: string;
  window: string;
  note?: string;
  agg: Agg;
  latency: ({ method: string } & Record<string, number>)[];
}
interface Props { milestones: Milestone[]; pending?: string[] }

// Per-endpoint latency with a milestone switcher (auto-cycles, or click a pill).
export default function MilestoneLatency({ milestones, pending = [] }: Props) {
  const { idx, pick } = useAutoCycle(milestones.length);
  const m = milestones[idx]!;
  return (
    <div>
      <MilestonePills labels={milestones.map((x) => x.label)} active={idx} onPick={pick} pending={pending} />
      {m.note && (
        <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200/90">{m.note}</p>
      )}
      <EndpointLatencyChart data={m.latency} />
      <p className="mt-2 text-xs text-ink-500">
        <span className="font-mono text-ink-300">{m.label} · {m.window}</span> — aggregate p99{' '}
        <span style={{ color: '#ff9900' }}>Geth {m.agg.gethP99} ms</span> vs{' '}
        <span style={{ color: '#00b3ff' }}>Nethermind {m.agg.nmP99} ms</span> (mean {m.agg.gethMean} / {m.agg.nmMean} ms). 0 RPC failures either client.
      </p>
    </div>
  );
}
