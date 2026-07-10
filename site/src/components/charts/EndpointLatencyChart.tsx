import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, BRAND } from './echarts-base';

type Row = { method: string } & Record<string, number>;
interface Props { data: Row[]; clients?: string[]; unit?: string }

const COLORS: Record<string, string> = {
  nethermind: BRAND.blue,
  geth: BRAND.orange,
};

// Per-endpoint p99 read latency, Nethermind vs Geth, for a single milestone.
export default function EndpointLatencyChart({ data, clients = ['nethermind', 'geth'], unit = 'ms' }: Props) {
  const option = useMemo(() => {
    const cats = data.map((d) => d.method);
    const series = clients.map((c) => ({
      name: c,
      type: 'bar' as const,
      barWidth: '30%',
      itemStyle: { color: COLORS[c] ?? BRAND.textDim, borderRadius: [2, 2, 0, 0] },
      emphasis: { focus: 'series' as const },
      label: {
        show: true,
        position: 'top' as const,
        color: BRAND.textDim,
        fontSize: 11,
        formatter: (p: { value: number | null }) => (p.value == null ? '' : `${p.value} ${unit}`),
      },
      data: data.map((d) => d[c] ?? null),
    }));

    return {
      ...base({ legend: true, zoom: true }),
      legend: { ...base({ legend: true }).legend, data: clients },
      tooltip: { ...base().tooltip, valueFormatter: (v: number) => (v == null ? '—' : `${v} ${unit}`) },
      xAxis: catAxis('', { data: cats, axisLabel: { color: BRAND.textDim, rotate: 18, fontSize: 11 } }),
      yAxis: valAxis('p99 latency (log, ms)', { type: 'log', min: 1, axisLabel: { color: BRAND.textDim, formatter: `{value} ${unit}` } }),
      series,
    };
  }, [data, clients, unit]);

  return <EChart option={option} height={360} ariaLabel="Per-endpoint p99 latency, Nethermind vs Geth" />;
}
