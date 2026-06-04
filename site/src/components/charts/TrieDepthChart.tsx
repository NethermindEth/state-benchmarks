import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Level { depth: number; x1: number; x5: number }
interface Props { levels: Level[] }

const SERIES = [
  { key: 'x1', label: '1× mainnet', color: '#7dd3fc' },
  { key: 'x5', label: '5× bloated', color: '#fbbf24' },
] as const;

export default function TrieDepthChart({ levels }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 340, M = { top: 40, right: 24, bottom: 44, left: 48 };

    const t1 = d3.sum(levels, d => d.x1);
    const t5 = d3.sum(levels, d => d.x5);
    const pct = (d: Level, k: 'x1' | 'x5') => (d[k] / (k === 'x1' ? t1 : t5)) * 100;

    const x0 = d3.scaleBand().domain(levels.map(d => `${d.depth}`)).range([M.left, W - M.right]).paddingInner(0.25).paddingOuter(0.1);
    const x1 = d3.scaleBand().domain(SERIES.map(s => s.key)).range([0, x0.bandwidth()]).padding(0.08);
    const y = d3.scaleLinear().domain([0, d3.max(levels, d => Math.max(pct(d, 'x1'), pct(d, 'x5')))! * 1.12]).nice().range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x0))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 12))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));
    svg.append('text').attr('x', W / 2).attr('y', H - 6).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('trie depth (levels from root)');

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => `${d}%`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));

    svg.append('g').attr('opacity', 0.22)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right).attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#1b2032');

    levels.forEach((d, di) => {
      const g = svg.append('g').attr('transform', `translate(${x0(`${d.depth}`)},0)`);
      SERIES.forEach((s) => {
        const v = pct(d, s.key as 'x1' | 'x5');
        g.append('rect')
          .attr('x', x1(s.key)!).attr('y', y(0)).attr('width', x1.bandwidth()).attr('height', 0).attr('rx', 2)
          .attr('fill', s.color)
          .transition().delay(di * 40).duration(700).ease(d3.easeCubicOut)
          .attr('y', y(v)).attr('height', y(0) - y(v));
      });
    });

    const legend = svg.append('g').attr('transform', `translate(${W - M.right - 220},${M.top - 26})`);
    SERIES.forEach((s, i) => {
      const g = legend.append('g').attr('transform', `translate(${i * 120},0)`);
      g.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', s.color);
      g.append('text').attr('x', 18).attr('y', 11).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
    });
  }, [levels]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Account trie depth distribution, mainnet vs 5x" />;
}
