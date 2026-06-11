import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, LINE_TYPES, BRAND } from './echarts-base';

type Bucket = { bucket: string } & Record<string, number>;
interface Serie { key: string; label: string; color: string }
interface Props {
  buckets: Bucket[];
  series?: Serie[];
  pending?: string[];
}

const DEFAULT_SERIES: Serie[] = [
  { key: 'x1', label: '1× mainnet', color: BRAND.blue },
  { key: 'x35', label: '3.5×', color: BRAND.orange },
  { key: 'x5', label: '5×', color: BRAND.green },
];

export default function SlotHistogramChart({ buckets, series = DEFAULT_SERIES, pending = [] }: Props) {
  const option = useMemo(() => {
    const cats = buckets.map((b) => b.bucket);
    const lineSeries = series.map((s, i) => ({
      name: s.label,
      type: 'line' as const,
      smooth: true,
      symbol: 'circle',
      symbolSize: 7 - i,
      lineStyle: { width: 3 - i * 0.6, type: LINE_TYPES[i % 3] },
      itemStyle: { color: s.color },
      emphasis: { focus: 'series' as const },
      data: buckets.map((b) => b[s.key] ?? null),
    }));
    const pendingSeries = pending.map((p) => ({
      name: `${p} pending`,
      type: 'line' as const,
      data: [] as number[],
      lineStyle: { type: 'dotted' as const, color: BRAND.pending },
      itemStyle: { color: BRAND.pending },
    }));

    return {
      ...base({ legend: true, zoom: true }),
      legend: { ...base({ legend: true }).legend, data: [...series.map((s) => s.label), ...pending.map((p) => `${p} pending`)] },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? '—' : `${v.toLocaleString()} contracts`) },
      xAxis: catAxis('storage slots per contract', { data: cats, axisLabel: { color: BRAND.textDim, rotate: 35 } }),
      yAxis: valAxis('contracts (log scale)', { type: 'log', min: 1000, axisLabel: { color: BRAND.textDim, formatter: (v: number) => (v >= 1e6 ? `${v / 1e6}M` : v >= 1e3 ? `${v / 1e3}k` : `${v}`) } }),
      series: [...lineSeries, ...pendingSeries],
    };
  }, [buckets, series, pending]);

  return <EChart option={option} height={400} ariaLabel="Storage slots per contract across milestones" />;
}
