import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

type Bucket = { bucket: string } & Record<string, number>;
interface Serie { key: string; label: string; color: string }
interface Props {
  buckets: Bucket[];
  series?: Serie[];
  pending?: string[];
}

const DEFAULT_SERIES: Serie[] = [
  { key: 'x1', label: '1× mainnet', color: '#00b3ff' },
  { key: 'x35', label: '3.5×', color: '#ff9900' },
  { key: 'x5', label: '5×', color: '#34d399' },
];

export default function SlotHistogramChart({ buckets, series = DEFAULT_SERIES, pending = [] }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 760, H = 400, M = { top: 44, right: 20, bottom: 66, left: 58 };

    const x = d3.scalePoint<string>().domain(buckets.map(b => b.bucket)).range([M.left, W - M.right]).padding(0.4);
    const maxV = d3.max(buckets, b => d3.max(series, s => b[s.key] ?? 0))!;
    const y = d3.scaleLog().domain([1000, maxV * 1.3]).range([H - M.bottom, M.top]).clamp(true);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x))
      .call(g => g.selectAll('text').attr('fill', '#aab2c2').attr('font-size', 11).attr('transform', 'rotate(-35)').attr('text-anchor', 'end').attr('dx', '-2').attr('dy', '6'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('x', (M.left + W - M.right) / 2).attr('y', H - 6).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('storage slots per contract');

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5, '~s'))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#13405c'));
    svg.append('text').attr('transform', 'rotate(-90)').attr('x', -(M.top + H - M.bottom) / 2).attr('y', 14).attr('text-anchor', 'middle').attr('fill', '#7a839a').attr('font-size', 11).text('contracts (log scale)');

    svg.append('g').attr('opacity', 0.18)
      .selectAll('line').data(y.ticks(5)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right).attr('y1', d => y(d)).attr('y2', d => y(d)).attr('stroke', '#001a2c');

    // distinct styling so near-coincident lines stay separable
    const DASH = ['none', '8 4', '2 4'];
    const SW = [3.4, 2.4, 1.6];
    const MR = [5.5, 3.6, 2];
    series.forEach((s, si) => {
      const dash = DASH[si % 3];
      const line = d3.line<Bucket>().x(d => x(d.bucket)!).y(d => y(d[s.key] ?? 1)).curve(d3.curveMonotoneX);
      const path = svg.append('path').datum(buckets).attr('fill', 'none').attr('stroke', s.color)
        .attr('stroke-width', SW[si] ?? 2).attr('opacity', 0.95).attr('d', line);
      const len = (path.node() as SVGPathElement).getTotalLength();
      path.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len)
        .transition().delay(si * 150).duration(900).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
        .on('end', () => path.attr('stroke-dasharray', dash === 'none' ? null : dash));
      const dots = svg.append('g').selectAll('circle').data(buckets).join('circle')
        .attr('cx', d => x(d.bucket)!).attr('cy', d => y(d[s.key] ?? 1)).attr('r', 0)
        .attr('fill', si === series.length - 1 ? s.color : 'none')
        .attr('stroke', s.color).attr('stroke-width', 1.5);
      dots.append('title').text(d => `${s.label}, ${d.bucket} slots: ${(d[s.key] ?? 0).toLocaleString()} contracts`);
      dots.transition().delay(si * 150 + 700).duration(250).attr('r', MR[si] ?? 3);
    });

    const legend = svg.append('g').attr('transform', `translate(${M.left + 4},${M.top - 30})`);
    let lx = 0;
    series.forEach((s, si) => {
      const g = legend.append('g').attr('transform', `translate(${lx},0)`);
      g.append('line').attr('x1', 0).attr('x2', 18).attr('y1', 6).attr('y2', 6).attr('stroke', s.color).attr('stroke-width', SW[si] ?? 2.5).attr('stroke-dasharray', DASH[si % 3] === 'none' ? null : DASH[si % 3]);
      g.append('text').attr('x', 24).attr('y', 10).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
      lx += 30 + s.label.length * 7.5;
    });
    pending.forEach((p) => {
      const g = legend.append('g').attr('transform', `translate(${lx},0)`);
      g.append('line').attr('x1', 0).attr('x2', 18).attr('y1', 6).attr('y2', 6).attr('stroke', '#3b5168').attr('stroke-width', 2.5).attr('stroke-dasharray', '3 3');
      g.append('text').attr('x', 24).attr('y', 10).attr('fill', '#525c75').attr('font-size', 12).text(`${p} pending`);
      lx += 38 + p.length * 7.5;
    });
  }, [buckets, series, pending]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="Storage slots per contract across milestones" />;
}
