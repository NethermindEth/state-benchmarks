import { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Row {
  mult: number;
  accountP50: number;
  accountP95: number;
  storageP50: number;
  storageP95: number;
}
interface Props { data: Row[] }

export default function ProofSizeChart({ data }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();
    const W = 720, H = 340, M = { top: 24, right: 24, bottom: 40, left: 64 };

    const x = d3.scaleLinear().domain([1, 10]).range([M.left, W - M.right]);
    const y = d3.scaleLinear()
      .domain([0, d3.max(data, d => d.accountP95)! * 1.1])
      .nice()
      .range([H - M.bottom, M.top]);

    svg.attr('viewBox', `0 0 ${W} ${H}`);

    svg.append('g').attr('transform', `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x).ticks(8).tickFormat(d => `${d}x`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));

    svg.append('g').attr('transform', `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(6).tickFormat(d => `${(+d / 1024).toFixed(1)} KB`))
      .call(g => g.selectAll('text').attr('fill', '#7a839a'))
      .call(g => g.selectAll('line, path').attr('stroke', '#2b3247'));

    svg.append('g').attr('opacity', 0.25)
      .selectAll('line').data(y.ticks(6)).join('line')
      .attr('x1', M.left).attr('x2', W - M.right)
      .attr('y1', d => y(d)).attr('y2', d => y(d))
      .attr('stroke', '#1b2032');

    const series: Array<{ key: keyof Row; color: string; dash: string | null; label: string }> = [
      { key: 'accountP50', color: '#7dd3fc', dash: null,  label: 'account proof p50' },
      { key: 'accountP95', color: '#7dd3fc', dash: '4 3', label: 'account proof p95' },
      { key: 'storageP50', color: '#fbbf24', dash: null,  label: 'storage proof p50' },
      { key: 'storageP95', color: '#fbbf24', dash: '4 3', label: 'storage proof p95' },
    ];

    series.forEach((s, i) => {
      const line = d3.line<Row>()
        .x(d => x(d.mult))
        .y(d => y(d[s.key] as number))
        .curve(d3.curveMonotoneX);

      const path = svg.append('path')
        .datum(data)
        .attr('fill', 'none')
        .attr('stroke', s.color)
        .attr('stroke-width', 2.25)
        .attr('stroke-dasharray', s.dash ?? '')
        .attr('d', line);

      const len = (path.node() as SVGPathElement).getTotalLength();
      path.attr('stroke-dasharray', `${len} ${len}`)
        .attr('stroke-dashoffset', len)
        .transition().delay(i * 120).duration(1000).ease(d3.easeCubicOut)
        .attr('stroke-dashoffset', 0)
        .on('end', () => { if (s.dash) path.attr('stroke-dasharray', s.dash); else path.attr('stroke-dasharray', null); });
    });

    const legend = svg.append('g').attr('transform', `translate(${M.left + 8},${M.top + 4})`);
    series.forEach((s, i) => {
      const g = legend.append('g').attr('transform', `translate(${(i % 2) * 200},${Math.floor(i / 2) * 18})`);
      g.append('line').attr('x1', 0).attr('x2', 20).attr('y1', 6).attr('y2', 6)
        .attr('stroke', s.color).attr('stroke-width', 2.25).attr('stroke-dasharray', s.dash ?? '');
      g.append('text').attr('x', 28).attr('y', 10).attr('fill', '#aab2c2').attr('font-size', 12).text(s.label);
    });
  }, [data]);

  return <svg ref={ref} className="w-full h-auto" role="img" aria-label="eth_getProof size across multipliers" />;
}
