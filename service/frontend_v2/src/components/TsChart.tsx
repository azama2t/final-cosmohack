import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ModelInfo, TsRow } from '../types';
import { ACCENT, fmtDate, fmtPermille, modelLabel } from '../lib/style';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

interface Props {
  rows: TsRow[];
  model: string;
  date: string;
  models: Record<string, ModelInfo>;
  onDate: (d: string) => void;
}

/** Index by date: current model — accent line (findings), the other model — thin neutral dashed. */
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
      return {
        type: 'line' as const,
        name: modelLabel(id, models[id]?.name),
        data: dates.map((d) => [d, rows.find((x) => x.date === d && x.model === id)?.mean_index ?? null]),
        connectNulls: false,
        smooth: false,
        symbol: 'circle',
        symbolSize: (v: any) => (on && v[0] === date ? 8 : on ? 5 : 3),
        z: on ? 3 : 2,
        lineStyle: { width: on ? 1.5 : 1, color: on ? ACCENT : '#6f757d', type: on ? ('solid' as const) : ('dashed' as const) },
        itemStyle: { color: on ? ACCENT : '#6f757d', borderColor: on ? '#0f1012' : '#6f757d', borderWidth: on ? 1 : 0 },
        markLine: on
          ? {
              silent: true,
              symbol: 'none',
              label: { show: false },
              lineStyle: { color: 'rgba(255,255,255,0.18)', type: 'solid' as const, width: 1 },
              data: [{ xAxis: date }],
            }
          : undefined,
      };
    });
    c.setOption(
      {
        animationDuration: 250,
        animationEasing: 'cubicOut',
        textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
        grid: { left: 0, right: 8, top: 8, bottom: 0, containLabel: true },
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#0f1012',
          borderColor: 'rgba(255,255,255,0.13)',
          borderRadius: 4,
          padding: [6, 10],
          textStyle: { color: '#e7e8ea', fontSize: 12 },
          formatter: (ps: any[]) =>
            `${fmtDate(ps[0].data[0])}<br/>` + ps.map((p) => `${p.seriesName}: ${fmtPermille(p.data[1])} ‰`).join('<br/>'),
        },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.13)' } },
          axisTick: { show: false },
          axisLabel: { color: '#6f757d', fontSize: 10, formatter: (d: string) => d.slice(2).split('-').reverse().slice(1).join('.') },
        },
        yAxis: {
          type: 'value',
          min: 0,
          splitNumber: 2,
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
          axisLabel: { color: '#6f757d', fontSize: 10 },
        },
        series,
      },
      true,
    );
  }, [rows, model, date, models]);

  return <div ref={el} className="chart" data-testid="ts-chart" />;
}
