import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Row { mult: number; totalGB?: number; measured?: boolean; pending?: boolean; compositionPending?: boolean; slotNote?: string }
interface Props { data: Row[] }

export default function StateGrowthChart({ data }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 360, M = { top: 40, right: 24, bottom: 44, left: 64 };

    const x = d3.scaleBand()
      .domain(data.map(d => `${d.mult}x`))
      .range([M.left, W - M.right])
      .padding(0.4);

    const y = d3.scaleLinear()
      .domain([0, d3.max(data, d => d.totalGB ?? 0)! * 1.12])
      .nice()
      .range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    // hatch for projected
    const defs = svg.append('defs');
    const grad = defs.append('linearGradient').attr('id', 'sg-bar').attr('x1', 0).attr('x2', 0).attr('y1', 0).attr('y2', 1);
    grad.append('stop').attr('offset', '0%').attr('stop-color', '#7dd3fc');
    grad.append('stop').attr('offset', '100%').attr('stop-color', '#0ea5e9');
    const pat = defs.append('pattern').attr('id', 'sg-hatch')
      .attr('width', 7).attr('height', 7).attr('patternUnits', 'userSpaceOnUse').attr('patternTransform', 'rotate(45)');
    pat.append('rect').attr('width', 7).attr('height', 7).attr('fill', '#13283a');
    pat.append('line').attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 7).attr('stroke', '#38bdf8').attr('stroke-width', 3).attr('opacity', 0.5);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 12))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => `${(+d / 1000).toFixed(1)} TB`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));

    svg.append('g').attr('opacity', 0.22)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right)
      .attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#1b2032');

    data.forEach((d, i) => {
      const bx = x(`${d.mult}x`)!;
      const bw = x.bandwidth();

      if (d.pending || d.totalGB == null) {
        // empty reserved slot — no fabricated value
        svg.append('rect')
          .attr('x', bx).attr('y', M.top).attr('width', bw).attr('height', H - M.bottom - M.top)
          .attr('rx', 4).attr('fill', 'none')
          .attr('stroke', '#2b3247').attr('stroke-dasharray', '4 4');
        svg.append('text')
          .attr('x', bx + bw / 2).attr('y', (M.top + H - M.bottom) / 2)
          .attr('text-anchor', 'middle').attr('fill', '#525c75').attr('font-size', 11)
          .attr('font-family', 'JetBrains Mono').text('no scan');
        svg.append('text')
          .attr('x', bx + bw / 2).attr('y', H - M.bottom + 34).attr('text-anchor', 'middle')
          .attr('fill', '#525c75').attr('font-size', 9).attr('font-family', 'JetBrains Mono')
          .text(d.slotNote ?? 'pending');
        return;
      }

      svg.append('rect')
        .attr('x', bx).attr('y', y(0)).attr('width', bw).attr('height', 0).attr('rx', 4)
        .attr('fill', 'url(#sg-bar)')
        .transition().delay(i * 70).duration(850).ease(d3.easeCubicOut)
        .attr('y', y(d.totalGB!)).attr('height', y(0) - y(d.totalGB!));

      svg.append('text')
        .attr('x', bx + bw / 2).attr('y', y(d.totalGB!) - 8).attr('text-anchor', 'middle')
        .attr('fill', '#e5e9f0').attr('font-size', 12).attr('font-weight', 600).attr('opacity', 0)
        .text(d.totalGB! >= 1000 ? `${(d.totalGB! / 1000).toFixed(2)} TB` : `${Math.round(d.totalGB!)} GB`)
        .transition().delay(i * 70 + 500).duration(300).attr('opacity', 1);

      svg.append('text')
        .attr('x', bx + bw / 2).attr('y', H - M.bottom + 34).attr('text-anchor', 'middle')
        .attr('fill', '#34d399').attr('font-size', 9).attr('font-family', 'JetBrains Mono')
        .text('measured');
    });
  }, [data]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Total state size across milestones" />;
}
