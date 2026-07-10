import { useAutoCycle } from './useAutoCycle';
import MilestonePills from './MilestonePills.tsx';
import AdvantageChart from './AdvantageChart.tsx';

interface Row { metric: string; geth: string; nethermind: string; winner: 'geth' | 'nethermind'; ratio: number }
interface Milestone { mult: number; label: string; window: string; execution: Row[] }
interface Props { milestones: Milestone[]; pending?: string[] }

// Execution & resource "opposite optima" advantage chart with a milestone
// switcher (auto-cycles, or click a pill). Raw values in the table below switch
// with the chart.
export default function MilestoneAdvantage({ milestones, pending = [] }: Props) {
  const { idx, pick } = useAutoCycle(milestones.length);
  const m = milestones[idx]!;
  return (
    <div>
      <MilestonePills labels={milestones.map((x) => x.label)} active={idx} onPick={pick} pending={pending} />
      <p className="text-xs font-mono text-ink-500 mb-2">{m.label} · {m.window}</p>
      <AdvantageChart data={m.execution} />
      <div className="overflow-x-auto rounded-xl border border-ink-800 mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-ink-900 text-ink-300 text-left">
              <th className="px-3 py-2 font-medium">Metric</th>
              <th className="px-3 py-2 font-medium text-right">Geth</th>
              <th className="px-3 py-2 font-medium text-right">Nethermind</th>
              <th className="px-3 py-2 font-medium text-center">Winner</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {m.execution.map((r) => (
              <tr key={r.metric} className="border-t border-ink-800">
                <td className="px-3 py-2">{r.metric}</td>
                <td className={['px-3 py-2 text-right tabular-nums font-mono', r.winner === 'geth' ? 'text-white' : 'text-ink-300'].join(' ')}>{r.geth}</td>
                <td className={['px-3 py-2 text-right tabular-nums font-mono', r.winner === 'nethermind' ? 'text-white' : 'text-ink-300'].join(' ')}>{r.nethermind}</td>
                <td className="px-3 py-2 text-center">
                  <span className={['text-xs font-mono px-2 py-0.5 rounded', r.winner === 'geth' ? 'bg-accent-500/15 text-accent-300' : 'bg-signal-good/15 text-signal-good'].join(' ')}>{r.winner === 'geth' ? 'Geth' : 'NM'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
