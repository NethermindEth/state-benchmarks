import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Row { mult: number; accountP50: number; accountP95: number; storageP50: number; measured?: boolean }
interface Props { data: Row[]; pendingMults?: number[] }

const SERIES = [
  { key: 'accountP50', label: 'account p50', color: '#00b3ff' },
  { key: 'accountP95', label: 'account p95', color: '#4fc9ff' },
  { key: 'storageP50', label: 'account + 1 slot p50', color: '#ff9900' },
] as const;

export default function ProofComparisonChart({ data, pendingMults = [] }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 360, M = { top: 40, right: 24, bottom: 44, left: 56 };

    const allMults = [...data.map(d => d.mult), ...pendingMults].sort((a, b) => a - b);
    const x0 = d3.scaleBand().domain(allMults.map(m => `${m}x`)).range([M.left, W - M.right]).paddingInner(0.3).paddingOuter(0.1);
    const x1 = d3.scaleBand().domain(SERIES.map(s => s.key)).range([0, x0.bandwidth()]).padding(0.12);

    const y = d3.scaleLinear()
      .domain([0, d3.max(data, d => Math.max(d.accountP95, d.storageP50))! * 1.18])
      .nice()
      .range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x0))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 12))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => `${(+d / 1024).toFixed(1)} KB`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    svg.append('g').attr('opacity', 0.22)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right)
      .attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#001a2c');

    // pending slots
    pendingMults.forEach((m) => {
      const bx = x0(`${m}x`)!;
      svg.append('rect').attr('x', bx).attr('y', M.top).attr('width', x0.bandwidth()).attr('height', y(0) - M.top)
        .attr('fill', 'none').attr('stroke', '#13405c').attr('stroke-dasharray', '4 4');
      svg.append('text').attr('x', bx + x0.bandwidth() / 2).attr('y', (M.top + y(0)) / 2)
        .attr('text-anchor', 'middle').attr('fill', '#525c75').attr('font-size', 11).attr('font-family', 'JetBrains Mono').text('pending');
      svg.append('text').attr('x', bx + x0.bandwidth() / 2).attr('y', H - M.bottom + 34)
        .attr('text-anchor', 'middle').attr('fill', '#525c75').attr('font-size', 9).attr('font-family', 'JetBrains Mono').text('not sampled');
    });

    data.forEach((d, di) => {
      const g = svg.append('g').attr('transform', `translate(${x0(`${d.mult}x`)},0)`);
      SERIES.forEach((s) => {
        g.append('rect')
          .attr('x', x1(s.key)!).attr('y', y(0)).attr('width', x1.bandwidth()).attr('height', 0).attr('rx', 2)
          .attr('fill', s.color)
          .transition().delay(di * 80).duration(800).ease(d3.easeCubicOut)
          .attr('y', y((d as any)[s.key])).attr('height', y(0) - y((d as any)[s.key]));
      });
      svg.append('text').attr('x', x0(`${d.mult}x`)! + x0.bandwidth() / 2).attr('y', H - M.bottom + 34)
        .attr('text-anchor', 'middle').attr('fill', '#34d399').attr('font-size', 9).attr('font-family', 'JetBrains Mono').text('measured');
    });

    const legend = svg.append('g').attr('transform', `translate(${M.left + 4},${M.top - 26})`);
    SERIES.forEach((s, i) => {
      const g = legend.append('g').attr('transform', `translate(${i * 150},0)`);
      g.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', s.color);
      g.append('text').attr('x', 18).attr('y', 11).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
    });
  }, [data, pendingMults]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="eth_getProof size across measured milestones" />;
}
