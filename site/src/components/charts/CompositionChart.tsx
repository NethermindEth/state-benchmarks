import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, BRAND } from './echarts-base';

interface Row {
  mult: number;
  acctGB?: number;
  storGB?: number;
  codeGB?: number;
  totalGB?: number;
  pending?: boolean;
}
interface Props { data: Row[] }

const AXES = [
  { key: 'storGB', label: 'storage', color: BRAND.blue },
  { key: 'acctGB', label: 'account', color: BRAND.orange },
  { key: 'codeGB', label: 'code', color: BRAND.green },
] as const;

export default function CompositionChart({ data }: Props) {
  const option = useMemo(() => {
    const cats = data.map((d) => `${d.mult}×`);
    const gb = (v: number | null) => (v == null ? 'pending' : v >= 1000 ? `${(v / 1000).toFixed(2)} TB` : `${Math.round(v)} GB`);
    const series = AXES.map((a) => ({
      name: a.label,
      type: 'bar' as const,
      stack: 'total',
      barWidth: '52%',
      itemStyle: { color: a.color },
      emphasis: { focus: 'series' as const },
      data: data.map((d) => d[a.key] ?? null),
    }));
    return {
      ...base({ legend: true, zoom: true }),
      legend: { ...base({ legend: true }).legend, data: AXES.map((a) => a.label) },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => gb(v) },
      xAxis: catAxis('', { data: cats }),
      yAxis: valAxis('trie size', { axisLabel: { color: BRAND.textDim, formatter: (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} TB` : `${v} GB`) } }),
      series,
    };
  }, [data]);

  return <EChart option={option} height={360} ariaLabel="State composition share across milestones" />;
}
