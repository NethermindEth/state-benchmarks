import { useMemo } from 'react';
import EChart from './EChart.tsx';
import { base, valAxis, BRAND } from './echarts-base';

interface Row { metric: string; geth: string; nethermind: string; winner: 'geth' | 'nethermind'; ratio: number }
interface Props { data: Row[] }

const NM = BRAND.blue;
const GETH = BRAND.orange;

// Diverging bars: each metric's winner extends toward its side, length = how many×
// it beats the other client. Geth-leaner metrics point left, NM-faster point right —
// the two clusters make the "opposite optima" legible at a glance.
export default function AdvantageChart({ data }: Props) {
  const option = useMemo(() => {
    // order so Geth-advantage (negative) sits at the bottom, NM (positive) at top
    const rows = [...data].sort((a, b) => {
      const av = a.winner === 'nethermind' ? a.ratio : -a.ratio;
      const bv = b.winner === 'nethermind' ? b.ratio : -b.ratio;
      return av - bv;
    });
    const cats = rows.map((r) => r.metric);
    const max = Math.ceil(Math.max(...rows.map((r) => r.ratio)) + 0.5);

    return {
      ...base({ legend: false }),
      grid: { top: 30, right: 80, bottom: 28, left: 130, containLabel: true },
      tooltip: {
        ...base().tooltip,
        trigger: 'item' as const,
        formatter: (p: { dataIndex: number }) => {
          const r = rows[p.dataIndex]!;
          const w = r.winner === 'nethermind' ? 'Nethermind' : 'Geth';
          return `${r.metric}<br/>Geth ${r.geth} · NM ${r.nethermind}<br/><b>${w} ${r.ratio}× better</b>`;
        },
      },
      xAxis: valAxis('← Geth leaner   ·   NM higher-throughput →', {
        min: -max, max,
        axisLabel: { color: BRAND.textDim, formatter: (v: number) => (v === 0 ? '0' : `${Math.abs(v)}×`) },
        splitLine: { lineStyle: { color: BRAND.gridFaint } },
      }),
      yAxis: {
        type: 'category' as const, data: cats,
        axisLine: { lineStyle: { color: BRAND.grid } }, axisTick: { show: false },
        axisLabel: { color: BRAND.text, fontSize: 12 },
      },
      series: [{
        type: 'bar' as const,
        barWidth: '58%',
        data: rows.map((r) => {
          const signed = r.winner === 'nethermind' ? r.ratio : -r.ratio;
          const color = r.winner === 'nethermind' ? NM : GETH;
          return {
            value: signed,
            itemStyle: { color, borderRadius: r.winner === 'nethermind' ? [0, 3, 3, 0] : [3, 0, 0, 3] },
            label: {
              show: true,
              position: r.winner === 'nethermind' ? ('right' as const) : ('left' as const),
              color,
              fontFamily: 'ui-monospace, monospace',
              fontSize: 11,
              formatter: `${r.ratio}×`,
            },
          };
        }),
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: BRAND.grid, type: 'solid' as const, width: 1 },
          data: [{ xAxis: 0 }],
        },
      }],
    };
  }, [data]);

  return <EChart option={option} height={340} ariaLabel="Per-metric advantage, Geth vs Nethermind" />;
}
