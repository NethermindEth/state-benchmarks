import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Bucket { bucket: string; mainnet: number; x5: number }
interface Props { buckets: Bucket[] }

const SERIES = [
  { key: 'mainnet', label: '1× mainnet', color: '#00b3ff' },
  { key: 'x5', label: '5× bloated', color: '#ff9900' },
] as const;

const fmt = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(0)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : `${n}`;

export default function SlotHistogramChart({ buckets }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 760, H = 380, M = { top: 36, right: 20, bottom: 64, left: 56 };

    const x0 = d3.scaleBand().domain(buckets.map(b => b.bucket)).range([M.left, W - M.right]).paddingInner(0.22).paddingOuter(0.08);
    const x1 = d3.scaleBand().domain(SERIES.map(s => s.key)).range([0, x0.bandwidth()]).padding(0.1);

    // log y — counts span 4k → 18M
    const y = d3.scaleLog().domain([1000, d3.max(buckets, b => Math.max(b.mainnet, b.x5))! * 1.3]).range([H - M.bottom, M.top]).clamp(true);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x0))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 11).attr('transform', 'rotate(-35)').attr('text-anchor', 'end').attr('dx', '-2').attr('dy', '6'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('x', (M.left + W - M.right) / 2).attr('y', H - 6).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('storage slots per contract');

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5, '~s'))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('transform', 'rotate(-90)').attr('x', -(M.top + H - M.bottom) / 2).attr('y', 14).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('contracts (log scale)');

    svg.append('g').attr('opacity', 0.2)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right).attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#001a2c');

    buckets.forEach((b, bi) => {
      const g = svg.append('g').attr('transform', `translate(${x0(b.bucket)},0)`);
      SERIES.forEach((s) => {
        const v = (b as any)[s.key] as number;
        const rect = g.append('rect')
          .attr('x', x1(s.key)!).attr('y', y(1000)).attr('width', x1.bandwidth()).attr('height', 0).attr('rx', 1.5)
          .attr('fill', s.color);
        rect.append('title').text(`${s.label}, ${b.bucket} slots: ${v.toLocaleString()} contracts`);
        rect.transition().delay(bi * 35).duration(700).ease(d3.easeCubicOut)
          .attr('y', y(v)).attr('height', y(1000) - y(v));
      });
    });

    const legend = svg.append('g').attr('transform', `translate(${W - M.right - 220},${M.top - 26})`);
    SERIES.forEach((s, i) => {
      const g = legend.append('g').attr('transform', `translate(${i * 120},0)`);
      g.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', s.color);
      g.append('text').attr('x', 18).attr('y', 11).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
    });
  }, [buckets]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Storage slots per contract histogram, mainnet vs 5x" />;
}
