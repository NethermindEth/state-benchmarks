interface Props {
  labels: string[];
  active: number;
  onPick: (i: number) => void;
  pending?: string[];
}

// Milestone selector: active pill drives the chart below; auto-cycles until a
// pill is clicked. Pending milestones show as dashed, non-interactive chips.
export default function MilestonePills({ labels, active, onPick, pending = [] }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <span className="text-[11px] uppercase tracking-wider text-ink-500 mr-1">milestone</span>
      {labels.map((l, i) => (
        <button
          key={l}
          type="button"
          onClick={() => onPick(i)}
          aria-pressed={i === active}
          className={[
            'rounded-full px-3 py-1 text-sm font-mono border transition-colors',
            i === active
              ? 'border-accent-500 bg-accent-500/15 text-white'
              : 'border-ink-700 text-ink-300 hover:text-white hover:border-ink-500',
          ].join(' ')}
        >
          {l}
        </button>
      ))}
      {pending.map((p) => (
        <span
          key={p}
          className="rounded-full px-3 py-1 text-sm font-mono border border-dashed border-ink-800 text-ink-600"
          title="not measured yet"
        >
          {p} pending
        </span>
      ))}
    </div>
  );
}
