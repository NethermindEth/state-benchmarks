import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, BRAND } from './echarts-base';

type Row = { mult: number } & Record<string, number>;
interface TheoryPt { mult: number; y: number }
interface Props { data: Row[]; clients?: string[]; unit?: string; pendingMults?: number[]; theory?: TheoryPt[]; theoryLabel?: string }

const COLORS: Record<string, string> = {
  nethermind: BRAND.blue,
  geth: BRAND.orange,
  besu: '#f87171',
  reth: BRAND.green,
  erigon: '#c084fc',
};

export default function ClientComparisonChart({ data, clients = ['nethermind', 'geth'], unit = 'ms', pendingMults = [], theory = [], theoryLabel = 'model' }: Props) {
  const option = useMemo(() => {
    const mults = [...data.map((d) => d.mult), ...pendingMults].sort((a, b) => a - b);
    const cats = mults.map((m) => `${m}×`);
    const byMult = new Map(data.map((d) => [d.mult, d]));
    const theoryByMult = new Map(theory.map((t) => [t.mult, t.y]));

    const series: Record<string, unknown>[] = clients.map((c) => ({
      name: c,
      type: 'bar' as const,
      barWidth: clients.length > 2 ? '13%' : '24%',
      itemStyle: { color: COLORS[c] ?? BRAND.textDim, borderRadius: [2, 2, 0, 0] },
      emphasis: { focus: 'series' as const },
      data: mults.map((m) => (byMult.get(m) as Row | undefined)?.[c] ?? null),
    }));

    if (theory.length) {
      series.push({
        name: theoryLabel,
        type: 'line' as const,
        smooth: true,
        symbol: 'emptyCircle',
        symbolSize: 7,
        lineStyle: { color: 'rgba(230,241,248,0.6)', width: 1.8, type: 'dashed' as const },
        itemStyle: { color: 'rgba(230,241,248,0.6)' },
        z: 5,
        data: mults.map((m) => theoryByMult.get(m) ?? null),
      });
    }

    return {
      ...base({ legend: true, zoom: true }),
      legend: { ...base({ legend: true }).legend, data: [...clients, ...(theory.length ? [theoryLabel] : [])] },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? 'pending' : `${v} ${unit}`) },
      xAxis: catAxis('', { data: cats }),
      yAxis: valAxis('', { axisLabel: { color: BRAND.textDim, formatter: `{value} ${unit}` } }),
      series,
    };
  }, [data, clients, unit, pendingMults, theory, theoryLabel]);

  return <EChart option={option} height={360} ariaLabel="Client comparison across milestones" />;
}
