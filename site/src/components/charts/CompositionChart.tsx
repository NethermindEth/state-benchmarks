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
    const pct = (d: Row, k: 'storGB' | 'acctGB' | 'codeGB') => {
      const total = (d.acctGB ?? 0) + (d.storGB ?? 0) + (d.codeGB ?? 0);
      return total ? +(((d[k] ?? 0) / total) * 100).toFixed(1) : null;
    };
    const series = AXES.map((a) => ({
      name: a.label,
      type: 'bar' as const,
      stack: 'total',
      barWidth: '52%',
      itemStyle: { color: a.color },
      emphasis: { focus: 'series' as const },
      data: data.map((d) => pct(d, a.key)),
    }));
    return {
      ...base({ legend: true }),
      legend: { ...base({ legend: true }).legend, data: AXES.map((a) => a.label) },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? 'pending' : `${v}%`) },
      xAxis: catAxis('', { data: cats }),
      yAxis: valAxis('', { max: 100, axisLabel: { color: BRAND.textDim, formatter: '{value}%' } }),
      series,
    };
  }, [data]);

  return <EChart option={option} height={360} ariaLabel="State composition share across milestones" />;
}
