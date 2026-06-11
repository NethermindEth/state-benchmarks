import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

type Level = { depth: number } & Record<string, number>;
interface Serie { key: string; label: string; color: string }
interface Props {
  levels: Level[];
  series?: Serie[];
  pending?: string[];
}

const DEFAULT_SERIES: Serie[] = [
  { key: 'x1', label: '1× mainnet', color: '#00b3ff' },
  { key: 'x35', label: '3.5×', color: '#ff9900' },
  { key: 'x5', label: '5×', color: '#34d399' },
];

export default function TrieDepthChart({ levels, series = DEFAULT_SERIES, pending = [] }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 360, M = { top: 44, right: 24, bottom: 48, left: 52 };

    const totals: Record<string, number> = {};
    series.forEach((s) => { totals[s.key] = d3.sum(levels, (d) => d[s.key] ?? 0); });
    const pct = (d: Level, k: string) => (totals[k] ? ((d[k] ?? 0) / totals[k]) * 100 : 0);

    const x = d3.scalePoint<number>().domain(levels.map(d => d.depth)).range([M.left, W - M.right]).padding(0.5);
    const y = d3.scaleLinear().domain([0, d3.max(levels, d => Math.max(...series.map(s => pct(d, s.key))))! * 1.1]).nice().range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x).tickFormat(d => `${d}`))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 12))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('x', (M.left + W - M.right) / 2).attr('y', H - 8).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('trie depth (levels from root)');

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => `${d}%`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('transform', 'rotate(-90)').attr('x', -(M.top + H - M.bottom) / 2).attr('y', 14).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('% of leaves');

    svg.append('g').attr('opacity', 0.2)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right).attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#001a2c');

    series.forEach((s, si) => {
      const line = d3.line<Level>().x(d => x(d.depth)!).y(d => y(pct(d, s.key))).curve(d3.curveMonotoneX);
      const path = svg.append('path').datum(levels).attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 2.5).attr('d', line);
      const len = (path.node() as SVGPathElement).getTotalLength();
      path.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len)
        .transition().delay(si * 150).duration(900).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0);
      svg.append('g').selectAll('circle').data(levels).join('circle')
        .attr('cx', d => x(d.depth)!).attr('cy', d => y(pct(d, s.key))).attr('r', 0).attr('fill', s.color)
        .transition().delay(si * 150 + 700).duration(250).attr('r', 3);
    });

    // legend (measured series + pending milestones)
    const legend = svg.append('g').attr('transform', `translate(${M.left + 4},${M.top - 30})`);
    let lx = 0;
    series.forEach((s) => {
      const g = legend.append('g').attr('transform', `translate(${lx},0)`);
      g.append('line').attr('x1', 0).attr('x2', 18).attr('y1', 6).attr('y2', 6).attr('stroke', s.color).attr('stroke-width', 2.5);
      g.append('text').attr('x', 24).attr('y', 10).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
      lx += 30 + s.label.length * 7.5;
    });
    pending.forEach((p) => {
      const g = legend.append('g').attr('transform', `translate(${lx},0)`);
      g.append('line').attr('x1', 0).attr('x2', 18).attr('y1', 6).attr('y2', 6).attr('stroke', '#3b5168').attr('stroke-width', 2.5).attr('stroke-dasharray', '3 3');
      g.append('text').attr('x', 24).attr('y', 10).attr('fill', '#525c75').attr('font-size', 12).text(`${p} pending`);
      lx += 38 + p.length * 7.5;
    });
  }, [levels, series, pending]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Trie depth distribution across milestones" />;
}
