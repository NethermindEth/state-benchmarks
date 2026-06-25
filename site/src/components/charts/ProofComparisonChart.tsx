import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, BRAND, markPointFrom, markLineAt, type MarkNote } from './echarts-base';

interface Row { mult: number; accountP50: number; accountP95: number; storageP50: number; measured?: boolean }
interface Props { data: Row[]; pendingMults?: number[]; theory?: { mult: number; y: number }[]; notes?: MarkNote[]; baseline?: { y: number; text: string } }

const BARS = [
  { key: 'accountP50', label: 'account p50', color: BRAND.blue },
  { key: 'accountP95', label: 'account p95', color: BRAND.blueLight },
  { key: 'storageP50', label: 'account + 1 slot p50', color: BRAND.orange },
] as const;

export default function ProofComparisonChart({ data, pendingMults = [], theory = [], notes = [], baseline }: Props) {
  const option = useMemo(() => {
    const mults = [...data.map((d) => d.mult), ...pendingMults].sort((a, b) => a - b);
    const cats = mults.map((m) => `${m}×`);
    const byMult = new Map(data.map((d) => [d.mult, d]));
    const theoryByMult = new Map(theory.map((t) => [t.mult, t.y]));

    const barSeries = BARS.map((b) => ({
      name: b.label,
      type: 'bar' as const,
      barWidth: '20%',
      itemStyle: { color: b.color, borderRadius: [2, 2, 0, 0] },
      emphasis: { focus: 'series' as const },
      data: mults.map((m) => (byMult.get(m) as Row | undefined)?.[b.key] ?? null),
    }));

    if (notes.length) (barSeries[0] as Record<string, unknown>).markPoint = markPointFrom(notes);
    if (baseline) (barSeries[0] as Record<string, unknown>).markLine = markLineAt(baseline.y, baseline.text);

    const theorySeries = theory.length
      ? [{
          name: 'theory (log₁₆)',
          type: 'line' as const,
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#ffffff', width: 2, type: 'dashed' as const },
          itemStyle: { color: '#ffffff' },
          z: 5,
          data: mults.map((m) => theoryByMult.get(m) ?? null),
        }]
      : [];

    const legendData = [...BARS.map((b) => b.label), ...(theory.length ? ['theory (log₁₆)'] : [])];

    return {
      ...base({ legend: true, zoom: true }),
      legend: { ...base({ legend: true }).legend, data: legendData },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? '—' : `${(v / 1024).toFixed(2)} KB`) },
      xAxis: catAxis('', { data: cats }),
      yAxis: valAxis('proof size', { axisLabel: { color: BRAND.textDim, formatter: (v: number) => `${(v / 1024).toFixed(1)} KB` } }),
      series: [...barSeries, ...theorySeries],
    };
  }, [data, pendingMults, theory]);

  return <EChart option={option} height={380} ariaLabel="eth_getProof size across milestones" />;
}
