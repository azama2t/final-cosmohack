import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ModelInfo, TsRow } from '../types';
import { ACCENT, fmtDate, fmtPermille, modelLabel } from '../lib/style';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, LegendComponent, CanvasRenderer]);

interface Props {
  rows: TsRow[];
  model: string;
  date: string;
  models: Record<string, ModelInfo>;
  onDate: (d: string) => void;
}

export default function TsChart({ rows, model, date, models, onDate }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const onDateRef = useRef(onDate);
  onDateRef.current = onDate;

  useEffect(() => {
    const c = echarts.init(el.current!, undefined, { renderer: 'canvas' });
    chart.current = c;
    c.on('click', (e: any) => {
      const d = e?.data?.[0] ?? e?.name;
      if (typeof d === 'string') onDateRef.current(d);
    });
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current!);
    return () => {
      ro.disconnect();
      c.dispose();
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const dates = [...new Set(rows.map((r) => r.date))].sort();
    const ids = [...new Set(rows.map((r) => r.model))].sort((a, b) => (a === model ? 1 : b === model ? -1 : 0));
    const series = ids.map((id) => {
      const on = id === model;
      const data = dates.map((d) => {
        const r = rows.find((x) => x.date === d && x.model === id);
        return [d, r?.mean_index ?? null];
      });
      return {
        type: 'line' as const,
        name: modelLabel(id, models[id]?.name),
        data,
        connectNulls: false,
        smooth: 0.25,
        symbol: 'circle',
        symbolSize: (v: any) => (on && v[0] === date ? 11 : on ? 7 : 5),
        z: on ? 3 : 2,
        lineStyle: { width: on ? 2.5 : 1.5, color: on ? ACCENT : '#5f7594', type: on ? ('solid' as const) : ('dashed' as const) },
        itemStyle: {
          color: on ? ACCENT : '#5f7594',
          borderColor: on ? '#fff' : '#5f7594',
          borderWidth: on ? 1.5 : 0,
        },
        areaStyle: on
          ? {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(255,107,74,0.28)' },
                { offset: 1, color: 'rgba(255,107,74,0)' },
              ]),
            }
          : undefined,
        markLine: on
          ? {
              silent: true,
              symbol: 'none',
              label: { show: false },
              lineStyle: { color: 'rgba(255,255,255,0.25)', type: 'dashed' as const, width: 1 },
              data: [{ xAxis: date }],
            }
          : undefined,
      };
    });
    c.setOption(
      {
        animationDuration: 500,
        textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
        grid: { left: 8, right: 12, top: 28, bottom: 4, containLabel: true },
        legend: {
          top: 0,
          right: 0,
          itemWidth: 14,
          itemHeight: 8,
          textStyle: { color: '#8ea3bf', fontSize: 11 },
          data: ids.map((id) => modelLabel(id, models[id]?.name)).reverse(),
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(10,20,36,0.96)',
          borderColor: '#23395a',
          textStyle: { color: '#e6edf7', fontSize: 12 },
          formatter: (ps: any[]) =>
            `<b>${fmtDate(ps[0].data[0])}</b><br/>` +
            ps.map((p) => `${p.marker}${p.seriesName}: <b>${fmtPermille(p.data[1])} ‰</b>`).join('<br/>'),
        },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#23395a' } },
          axisTick: { show: false },
          axisLabel: { color: '#8ea3bf', fontSize: 11, formatter: (d: string) => d.slice(2).split('-').reverse().join('.') },
        },
        yAxis: {
          type: 'value',
          min: 0,
          splitNumber: 3,
          splitLine: { lineStyle: { color: 'rgba(120,150,190,0.12)' } },
          axisLabel: { color: '#8ea3bf', fontSize: 11 },
        },
        series,
      },
      true,
    );
  }, [rows, model, date, models]);

  return <div ref={el} className="chart" />;
}
