import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Point { x: number; y: number; measured?: boolean; label?: string }
interface Model {
  kind: 'linear' | 'log16';
  a?: number; b?: number;      // linear: y = a + b·x
  c0?: number; c1?: number;    // log16:  y = c0 + c1·log16(x / xRef)
  xRef?: number;
}
type Fmt = 'mult' | 'tb' | 'kb' | 'millions' | 'plain';
interface Props {
  points: Point[];
  model: Model;
  xDomain: [number, number];
  xLabel: string;
  yLabel: string;
  xFmt?: Fmt;
  yFmt?: Fmt;
}

function evalModel(m: Model, x: number): number {
  if (m.kind === 'linear') return (m.a ?? 0) + (m.b ?? 0) * x;
  return (m.c0 ?? 0) + (m.c1 ?? 0) * (Math.log(x / (m.xRef ?? 1)) / Math.log(16));
}

function fmtFor(kind: Fmt): (n: number) => string {
  switch (kind) {
    case 'mult': return (n) => `${n}×`;
    case 'tb': return (n) => `${(n / 1000).toFixed(1)} TB`;
    case 'kb': return (n) => `${(n / 1024).toFixed(1)} KB`;
    case 'millions': return (n) => `${Math.round(n)}M`;
    default: return (n) => `${n}`;
  }
}

export default function ModelFitChart({ points, model, xDomain, xLabel, yLabel, xFmt = 'plain', yFmt = 'plain' }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 340, M = { top: 36, right: 24, bottom: 48, left: 64 };

    const curve = d3.range(0, 121).map((i) => {
      const x = xDomain[0] + (i / 120) * (xDomain[1] - xDomain[0]);
      return { x, y: evalModel(model, x) };
    });

    const x = d3.scaleLinear().domain(xDomain).range([M.left, W - M.right]);
    const yMax = Math.max(d3.max(curve, d => d.y)!, d3.max(points, d => d.y)!) * 1.1;
    const y = d3.scaleLinear().domain([0, yMax]).nice().range([H - M.bottom, M.top]);

    const xfmt = fmtFor(xFmt);
    const yfmt = fmtFor(yFmt);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x).ticks(7).tickFormat(d => xfmt(+d)))
      .call(g => g.selectAll('text').attr('fill', '#7ba6c0'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('x', (M.left + W - M.right) / 2).attr('y', H - 8)
      .attr('text-anchor', 'middle').attr('fill', '#7ba6c0').attr('font-size', 11).text(xLabel);

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => yfmt(+d)))
      .call(g => g.selectAll('text').attr('fill', '#7ba6c0'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('transform', 'rotate(-90)').attr('x', -(M.top + H - M.bottom) / 2).attr('y', 16)
      .attr('text-anchor', 'middle').attr('fill', '#7ba6c0').attr('font-size', 11).text(yLabel);

    svg.append('g').attr('opacity', 0.22)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right).attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#001a2c');

    // theoretical curve (orange, animated draw-in)
    const line = d3.line<{ x: number; y: number }>().x(d => x(d.x)).y(d => y(d.y)).curve(d3.curveMonotoneX);
    const path = svg.append('path').datum(curve)
      .attr('fill', 'none').attr('stroke', '#ff9900').attr('stroke-width', 2.5).attr('d', line);
    const len = (path.node() as SVGPathElement).getTotalLength();
    path.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len)
      .transition().duration(1100).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0);

    // measured points (blue dots + labels)
    const pts = svg.append('g');
    points.forEach((p, i) => {
      pts.append('circle')
        .attr('cx', x(p.x)).attr('cy', y(p.y)).attr('r', 0)
        .attr('fill', p.measured === false ? '#00253d' : '#00b3ff')
        .attr('stroke', '#00b3ff').attr('stroke-width', 2)
        .transition().delay(700 + i * 90).duration(300).attr('r', 5);
      if (p.label) {
        pts.append('text')
          .attr('x', x(p.x)).attr('y', y(p.y) - 12).attr('text-anchor', 'middle')
          .attr('fill', '#d8e8f2').attr('font-size', 11).attr('font-weight', 600).attr('opacity', 0)
          .text(p.label)
          .transition().delay(900 + i * 90).duration(300).attr('opacity', 1);
      }
    });

    // legend
    const legend = svg.append('g').attr('transform', `translate(${M.left + 8},${M.top - 22})`);
    legend.append('line').attr('x1', 0).attr('x2', 22).attr('y1', 6).attr('y2', 6).attr('stroke', '#ff9900').attr('stroke-width', 2.5);
    legend.append('text').attr('x', 28).attr('y', 10).attr('fill', '#a9c8db').attr('font-size', 12).text('theoretical model');
    legend.append('circle').attr('cx', 178).attr('cy', 6).attr('r', 5).attr('fill', '#00b3ff');
    legend.append('text').attr('x', 190).attr('y', 10).attr('fill', '#a9c8db').attr('font-size', 12).text('measured');
  }, [points, model, xDomain, xFmt, yFmt]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Theoretical model fit against measured data" />;
}
