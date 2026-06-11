import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, catAxis, valAxis, BRAND } from './echarts-base';

interface Row { mult: number; totalGB?: number; measured?: boolean; pending?: boolean }
interface Props { data: Row[] }

export default function StateGrowthChart({ data }: Props) {
  const option = useMemo(() => {
    const cats = data.map((d) => `${d.mult}×`);
    return {
      ...base({ legend: false, zoom: true }),
      tooltip: {
        ...base({ legend: false }).tooltip,
        valueFormatter: (v: number) => (v == null ? 'pending' : v >= 1000 ? `${(v / 1000).toFixed(2)} TB` : `${Math.round(v)} GB`),
      },
      xAxis: catAxis('', { data: cats }),
      yAxis: valAxis('total trie size', { axisLabel: { color: BRAND.textDim, formatter: (v: number) => `${(v / 1000).toFixed(1)} TB` } }),
      series: [
        {
          name: 'total trie size',
          type: 'bar' as const,
          barWidth: '46%',
          itemStyle: {
            color: {
              type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{ offset: 0, color: BRAND.blue }, { offset: 1, color: '#0090d6' }],
            },
            borderRadius: [4, 4, 0, 0],
          },
          label: {
            show: true, position: 'top' as const, color: '#e6f1f8', fontSize: 12, fontWeight: 600 as const,
            formatter: (p: { value: number | null }) => (p.value == null ? '' : p.value >= 1000 ? `${(p.value / 1000).toFixed(2)} TB` : `${Math.round(p.value)} GB`),
          },
          data: data.map((d) => d.totalGB ?? null),
        },
      ],
    };
  }, [data]);

  return <EChart option={option} height={360} ariaLabel="Total state size across milestones" />;
}
