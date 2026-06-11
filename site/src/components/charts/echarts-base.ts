// Shared Nethermind-brand ECharts styling.

export const BRAND = {
  blue: '#00b3ff',
  orange: '#ff9900',
  green: '#34d399',
  blueLight: '#4fc9ff',
  text: '#aab2c2',
  textDim: '#7ba6c0',
  grid: '#13405c',
  gridFaint: '#001a2c',
  pending: '#3b5168',
};

// per-milestone palette (1× / 3.5× / 5× …) used across the milestone charts
export const MILESTONE_COLORS = [BRAND.blue, BRAND.orange, BRAND.green, '#c084fc', '#f472b6'];
export const LINE_TYPES = ['solid', 'dashed', 'dotted'] as const;

const axisCommon = {
  axisLine: { lineStyle: { color: BRAND.grid } },
  axisTick: { lineStyle: { color: BRAND.grid } },
  axisLabel: { color: BRAND.textDim, fontSize: 12 },
  nameTextStyle: { color: BRAND.textDim, fontSize: 11 },
  splitLine: { lineStyle: { color: BRAND.gridFaint } },
};

export function base(opts: { legend?: boolean; zoom?: boolean } = {}) {
  const { legend = true, zoom = false } = opts;
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'Inter, ui-sans-serif, sans-serif', color: BRAND.text },
    grid: { top: legend ? 56 : 36, right: 24, bottom: zoom ? 60 : 44, left: 60, containLabel: true },
    legend: legend
      ? {
          top: 12,
          left: 'center',
          textStyle: { color: BRAND.text, fontSize: 12 },
          inactiveColor: '#3b5168',
          icon: 'roundRect',
          itemWidth: 18,
          itemHeight: 10,
        }
      : undefined,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#001a2c',
      borderColor: BRAND.grid,
      borderWidth: 1,
      textStyle: { color: '#e6f1f8', fontSize: 12 },
      axisPointer: { type: 'cross', lineStyle: { color: BRAND.blue, opacity: 0.5 }, crossStyle: { color: BRAND.blue, opacity: 0.5 } },
    },
    toolbox: zoom
      ? {
          right: 14,
          top: 8,
          itemSize: 14,
          iconStyle: { borderColor: BRAND.textDim },
          emphasis: { iconStyle: { borderColor: BRAND.blue } },
          feature: {
            // drag a rectangle to zoom both axes; thin box = one-axis zoom
            dataZoom: { yAxisIndex: 0, title: { zoom: 'box zoom', back: 'undo zoom' } },
            restore: { title: 'reset' },
          },
        }
      : undefined,
    dataZoom: zoom
      ? [
          { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
          { type: 'inside', yAxisIndex: 0, filterMode: 'none' },
          {
            type: 'slider',
            xAxisIndex: 0,
            height: 16,
            bottom: 26,
            borderColor: BRAND.grid,
            backgroundColor: 'rgba(0,26,44,0.6)',
            fillerColor: 'rgba(0,179,255,0.15)',
            handleStyle: { color: BRAND.blue },
            moveHandleStyle: { color: BRAND.blue },
            textStyle: { color: BRAND.textDim },
            dataBackground: { lineStyle: { color: BRAND.grid }, areaStyle: { color: BRAND.gridFaint } },
            selectedDataBackground: { lineStyle: { color: BRAND.blue }, areaStyle: { color: 'rgba(0,179,255,0.15)' } },
          },
        ]
      : undefined,
  };
}

export function catAxis(name?: string, extra: Record<string, unknown> = {}) {
  return { type: 'category', name, nameLocation: 'middle', nameGap: 30, ...axisCommon, ...extra };
}

export function valAxis(name?: string, extra: Record<string, unknown> = {}) {
  return { type: 'value', name, nameLocation: 'middle', nameGap: 44, ...axisCommon, ...extra };
}
