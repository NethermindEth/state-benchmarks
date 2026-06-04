import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Row {
  mult: number;
  acctGB?: number;
  storGB?: number;
  codeGB?: number;
  totalGB?: number;
  measured?: boolean;
  pending?: boolean;
  slotNote?: string;
}
interface Props { data: Row[] }

const AXES = [
  { key: 'storGB', label: 'storage', color: '#00b3ff' },
  { key: 'acctGB', label: 'account', color: '#ff9900' },
  { key: 'codeGB', label: 'code', color: '#34d399' },
] as const;

export default function CompositionChart({ data }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 360, M = { top: 40, right: 24, bottom: 44, left: 48 };

    const x = d3.scaleBand()
      .domain(data.map(d => `${d.mult}x`))
      .range([M.left, W - M.right])
      .padding(0.35);

    const y = d3.scaleLinear().domain([0, 100]).range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    // y axis (percent)
    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => `${d}%`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    // x axis
    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 12))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));

    // hatch pattern for projected bars
    const defs = svg.append('defs');
    const pat = defs.append('pattern').attr('id', 'comp-hatch')
      .attr('width', 6).attr('height', 6).attr('patternUnits', 'userSpaceOnUse')
      .attr('patternTransform', 'rotate(45)');
    pat.append('rect').attr('width', 6).attr('height', 6).attr('fill', '#00111d');
    pat.append('line').attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 6).attr('stroke', '#13405c').attr('stroke-width', 4);

    data.forEach((d) => {
      const bx = x(`${d.mult}x`)!;
      const bw = x.bandwidth();

      if (d.pending || d.totalGB == null) {
        svg.append('rect')
          .attr('x', bx).attr('y', M.top).attr('width', bw).attr('height', y(0) - M.top)
          .attr('fill', 'none').attr('stroke', '#13405c').attr('stroke-dasharray', '4 4');
        svg.append('text')
          .attr('x', bx + bw / 2).attr('y', (M.top + y(0)) / 2).attr('text-anchor', 'middle')
          .attr('fill', '#525c75').attr('font-size', 11).attr('font-family', 'JetBrains Mono')
          .text('no scan');
        svg.append('text')
          .attr('x', bx + bw / 2).attr('y', H - M.bottom + 34).attr('text-anchor', 'middle')
          .attr('fill', '#525c75').attr('font-size', 9).attr('font-family', 'JetBrains Mono')
          .text(d.slotNote ?? 'pending');
        return;
      }

      const total = d.acctGB! + d.storGB! + d.codeGB!;
      let acc = 0;

      AXES.forEach((a) => {
        const pct = (d[a.key]! / total) * 100;
        const yTop = y(acc + pct);
        const yBot = y(acc);
        const seg = svg.append('g');
        seg.append('rect')
          .attr('x', bx)
          .attr('y', y(acc))
          .attr('width', bw)
          .attr('height', 0)
          .attr('fill', a.color)
          .attr('opacity', d.measured ? 0.95 : 0.4)
          .transition().duration(800).delay(80).ease(d3.easeCubicOut)
          .attr('y', yTop)
          .attr('height', Math.max(0, yBot - yTop));
        // percent label for the biggest two segments
        if (pct > 8) {
          seg.append('text')
            .attr('x', bx + bw / 2)
            .attr('y', (yTop + yBot) / 2 + 4)
            .attr('text-anchor', 'middle')
            .attr('fill', '#00111d')
            .attr('font-size', 11)
            .attr('font-weight', 600)
            .attr('opacity', 0)
            .text(`${pct.toFixed(0)}%`)
            .transition().delay(700).duration(300).attr('opacity', d.measured ? 1 : 0.7);
        }
        acc += pct;
      });

      // measured / projected tag under each bar
      svg.append('text')
        .attr('x', bx + bw / 2)
        .attr('y', H - M.bottom + 34)
        .attr('text-anchor', 'middle')
        .attr('fill', d.measured ? '#34d399' : '#525c75')
        .attr('font-size', 9)
        .attr('font-family', 'JetBrains Mono')
        .text(d.measured ? 'measured' : 'projected');
    });

    // legend
    const legend = svg.append('g').attr('transform', `translate(${M.left + 4},${M.top - 26})`);
    AXES.forEach((a, i) => {
      const g = legend.append('g').attr('transform', `translate(${i * 110},0)`);
      g.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', a.color);
      g.append('text').attr('x', 18).attr('y', 11).attr('fill', '#aab2c2').attr('font-size', 12).text(a.label);
    });
  }, [data]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="State composition share across milestones" />;
}
