import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, LINE_TYPES, BRAND } from './echarts-base';

type Level = { depth: number } & Record<string, number>;
interface Serie { key: string; label: string; color: string }
interface Props {
  levels: Level[];
  series?: Serie[];
  pending?: string[];
}

const DEFAULT_SERIES: Serie[] = [
  { key: 'x1', label: '1× mainnet', color: BRAND.blue },
  { key: 'x35', label: '3.5×', color: BRAND.orange },
  { key: 'x5', label: '5×', color: BRAND.green },
];

export default function TrieDepthChart({ levels, series = DEFAULT_SERIES, pending = [] }: Props) {
  const option = useMemo(() => {
    const depths = levels.map((d) => `${d.depth}`);
    const fmt = (v: number) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(0)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : `${v}`);

    const lineSeries = series.map((s, i) => ({
      name: s.label,
      type: 'line' as const,
      smooth: true,
      symbol: 'circle',
      symbolSize: 7 - i,
      lineStyle: { width: 3 - i * 0.6, type: LINE_TYPES[i % 3] },
      itemStyle: { color: s.color },
      emphasis: { focus: 'series' as const },
      data: levels.map((d) => (d[s.key] ?? 0) || null),
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
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? '—' : `${v.toLocaleString()} leaves`) },
      xAxis: catAxis('trie depth (levels from root)', { data: depths, boundaryGap: false }),
      yAxis: valAxis('leaves (log scale)', { type: 'log', min: 1, axisLabel: { color: BRAND.textDim, formatter: (v: number) => fmt(v) } }),
      series: [...lineSeries, ...pendingSeries],
    };
  }, [levels, series, pending]);

  return <EChart option={option} height={380} ariaLabel="Trie depth distribution across milestones" />;
}
