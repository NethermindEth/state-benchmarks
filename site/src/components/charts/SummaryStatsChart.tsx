import { useMemo, useState } from 'react';
import EChart from './EChart.tsx';
import { base, valAxis, BRAND } from './echarts-base';

interface XMode { key: string; label: string; axisName: string; kind: 'count' | 'gb'; values: number[] }
interface Serie { label: string; color: string; values: number[] }
interface Theory { label: string; formula: string; fit: string; points: Record<string, [number, number][]> }
interface Config { yName?: string; yLog?: boolean; xModes: XMode[]; series: Serie[]; theory?: Theory }
interface Props { data: Config }

const fmtCount = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)}B` : `${Math.round(v)}M`); // values are in millions
const fmtGB = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)} TB` : `${Math.round(v)} GB`);
const fmtBig = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : `${v}`);

export default function SummaryStatsChart({ data }: Props) {
  const [xi, setXi] = useState(0);
  const xMode = data.xModes[xi]!;
  const xFmt = xMode.kind === 'gb' ? fmtGB : fmtCount;

  const theoryPts = data.theory?.points[xMode.key] ?? null;

  const option = useMemo(() => {
    const yFmt = (v: number | null) => (v == null ? '—' : data.yLog ? fmtBig(v) : `${v}`);
    const xs = [...xMode.values, ...(theoryPts ? theoryPts.map((p) => p[0]) : [])];
    const xmin = Math.min(...xs), xmax = Math.max(...xs);
    const xpad = (xmax - xmin) * 0.04 || 1;
    const theorySerie = theoryPts
      ? [{
          name: data.theory!.label,
          type: 'line' as const,
          smooth: true,
          symbol: 'none' as const,
          z: 1,
          lineStyle: { width: 1.6, type: 'dashed' as const, color: 'rgba(230,241,248,0.55)' },
          itemStyle: { color: 'rgba(230,241,248,0.55)' },
          tooltip: { show: false },
          data: theoryPts,
        }]
      : [];
    return {
      ...base({ legend: true }),
      legend: { ...base({ legend: true }).legend, data: [...data.series.map((s) => s.label), ...(data.theory ? [data.theory.label] : [])] },
      tooltip: {
        ...base().tooltip,
        trigger: 'axis' as const,
        formatter: (ps: { seriesName: string; value: [number, number]; color: string }[]) => {
          const x = ps[0]?.value?.[0];
          const head = `${xMode.axisName}: ${xFmt(x ?? 0)}`;
          const rows = ps.map((p) => `${p.seriesName}: ${yFmt(p.value?.[1])}`).join('<br/>');
          return `${head}<br/>${rows}`;
        },
      },
      xAxis: {
        type: 'value' as const, scale: true, min: xmin - xpad, max: xmax + xpad,
        name: xMode.axisName, nameLocation: 'middle' as const, nameGap: 32,
        axisLine: { lineStyle: { color: BRAND.grid } }, axisTick: { lineStyle: { color: BRAND.grid } },
        axisLabel: { color: BRAND.textDim, fontSize: 11, formatter: (v: number) => xFmt(v) },
        nameTextStyle: { color: BRAND.textDim, fontSize: 11 }, splitLine: { lineStyle: { color: BRAND.gridFaint } },
      },
      yAxis: valAxis(data.yName, data.yLog
        ? { type: 'log', scale: true, axisLabel: { color: BRAND.textDim, formatter: (v: number) => fmtBig(v) } }
        : { scale: true, axisLabel: { color: BRAND.textDim } }),
      series: data.series.map((s) => ({
        name: s.label, type: 'line' as const, smooth: false, symbol: 'circle', symbolSize: 9,
        lineStyle: { width: 2.5, color: s.color }, itemStyle: { color: s.color },
        emphasis: { focus: 'series' as const },
        label: { show: data.series.length === 1, position: 'top' as const, color: BRAND.text, fontSize: 10, formatter: (p: { value: [number, number] }) => yFmt(p.value?.[1]) },
        z: 2,
        data: s.values.map((y, i) => [xMode.values[i], y]),
      })).concat(theorySerie),
    };
  }, [data, xi]);

  return (
    <div>
      <div className="flex justify-end gap-2 mb-2">
        {data.xModes.map((m, i) => (
          <button
            key={m.key}
            type="button"
            onClick={() => setXi(i)}
            className={[
              'rounded-full px-3 py-1 text-xs font-mono border transition-colors',
              i === xi ? 'border-accent-500 bg-accent-500/15 text-white' : 'border-ink-700 text-ink-400 hover:text-white hover:border-ink-500',
            ].join(' ')}
            aria-pressed={i === xi}
          >
            {m.label}
          </button>
        ))}
      </div>
      <EChart option={option} height={320} ariaLabel={data.yName ?? 'summary statistics'} />
      {data.theory && (
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
          <span className="font-mono text-ink-100">{data.theory.formula}</span>
          <span className="text-ink-500">{data.theory.fit}</span>
        </div>
      )}
    </div>
  );
}
