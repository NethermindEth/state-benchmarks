import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

type Row = { mult: number } & Record<string, number>;
interface Props { data: Row[]; clients?: string[]; unit?: string; pendingMults?: number[] }

const COLORS: Record<string, string> = {
  nethermind: '#00b3ff',
  geth: '#ff9900',
  besu: '#f87171',
  reth: '#34d399',
  erigon: '#c084fc',
};

export default function ClientComparisonChart({ data, clients: clientsProp, unit = 'ms', pendingMults = [] }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const clients = clientsProp ?? ['nethermind', 'geth'];
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 360, M = { top: 36, right: 24, bottom: 44, left: 56 };

    const allMults = [...data.map(d => d.mult), ...pendingMults].sort((a, b) => a - b);
    const x0 = d3.scaleBand()
      .domain(allMults.map(m => `${m}x`))
      .range([M.left, W - M.right])
      .paddingInner(0.25)
      .paddingOuter(0.1);

    const x1 = d3.scaleBand().domain(clients).range([0, x0.bandwidth()]).padding(0.08);

    const y = d3.scaleLinear()
      .domain([0, d3.max(data, d => d3.max(clients, c => d[c] ?? 0))! * 1.15])
      .nice()
      .range([H - M.bottom, M.top]);

    const color = (c: string) => COLORS[c] ?? '#7a839a';

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x0))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(6).tickFormat(d => `${d} ${unit}`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    svg.append('g').attr('opacity', 0.25)
      .selectAll('line').data(y.ticks(6)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right)
      .attr('y1', d => y(d)).attr('y2', d => y(d))
      .attr('stroke', '#001a2c');

    const groups = svg.append('g')
      .selectAll('g')
      .data(data)
      .join('g')
      .attr('transform', d => `translate(${x0(`${d.mult}x`)},0)`);

    clients.forEach((c) => {
      groups.append('rect')
        .attr('x', x1(c)!)
        .attr('y', y(0))
        .attr('width', x1.bandwidth())
        .attr('height', 0)
        .attr('fill', color(c))
        .attr('rx', 2)
        .transition()
        .delay((_, i) => i * 50)
        .duration(800)
        .ease(d3.easeCubicOut)
        .attr('y', d => y((d as Row)[c]))
        .attr('height', d => y(0) - y((d as Row)[c]));
    });

    // pending milestone slots
    pendingMults.forEach((m) => {
      const bx = x0(`${m}x`)!;
      svg.append('rect').attr('x', bx).attr('y', M.top).attr('width', x0.bandwidth()).attr('height', y(0) - M.top)
        .attr('fill', 'none').attr('stroke', '#13405c').attr('stroke-dasharray', '4 4');
      svg.append('text').attr('x', bx + x0.bandwidth() / 2).attr('y', (M.top + y(0)) / 2)
        .attr('text-anchor', 'middle').attr('fill', '#525c75').attr('font-size', 11).attr('font-family', 'JetBrains Mono').text('pending');
      svg.append('text').attr('x', bx + x0.bandwidth() / 2).attr('y', H - M.bottom + 32)
        .attr('text-anchor', 'middle').attr('fill', '#525c75').attr('font-size', 9).attr('font-family', 'JetBrains Mono').text('not measured');
    });

    const legend = svg.append('g').attr('transform', `translate(${M.left + 8},${M.top - 24})`);
    clients.forEach((c, i) => {
      const g = legend.append('g').attr('transform', `translate(${i * 110},0)`);
      g.append('rect').attr('width', 14).attr('height', 10).attr('y', 2).attr('rx', 2).attr('fill', color(c));
      g.append('text').attr('x', 20).attr('y', 11).attr('fill', '#aab2c2').attr('font-size', 12).text(c);
    });
  }, [data, clientsProp, unit, pendingMults]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Client execution time comparison" />;
}
