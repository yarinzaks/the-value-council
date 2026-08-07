"use client";

import { useMemo, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Card, NumCell, PctCell } from "./Cards";
import { useUI } from "./Providers";
import type { AgentSlug, BacktestMetrics } from "@/lib/types";
import { metaFor } from "@/lib/agents";

interface AgentForCompare {
  slug: AgentSlug;
  display_name: string;
  metrics: BacktestMetrics;
  benchmark: BacktestMetrics;
}

function normalize(metric: BacktestMetrics) {
  return {
    cagr: Math.max(0, Math.min(100, (metric.cagr_pct / 50) * 100)),
    sharpe: Math.max(0, Math.min(100, (metric.sharpe / 2.0) * 100)),
    sortino: Math.max(0, Math.min(100, (metric.sortino / 3.0) * 100)),
    calmar: Math.max(0, Math.min(100, (metric.calmar / 2.0) * 100)),
    drawdown: Math.max(
      0,
      Math.min(100, ((50 + metric.max_drawdown_pct) / 50) * 100),
    ),
    hit_rate: Math.max(0, Math.min(100, metric.hit_rate_monthly_pct)),
  };
}

export function CompareView({ agents }: { agents: AgentForCompare[] }) {
  const { t } = useUI();
  const allSlugs = agents.map((a) => a.slug);
  const [selected, setSelected] = useState<Set<AgentSlug>>(
    new Set(allSlugs.slice(0, Math.min(allSlugs.length, 4))),
  );

  const chartData = useMemo(() => {
    const dims = [
      { axis: t("col_cagr"), key: "cagr" },
      { axis: t("col_sharpe"), key: "sharpe" },
      { axis: t("col_sortino"), key: "sortino" },
      { axis: t("col_calmar"), key: "calmar" },
      { axis: t("col_max_dd"), key: "drawdown" },
      { axis: t("col_hit_rate"), key: "hit_rate" },
    ];
    const rows = dims.map((d) => {
      const row: Record<string, number | string> = { axis: d.axis };
      for (const a of agents) {
        if (!selected.has(a.slug)) continue;
        const norm = normalize(a.metrics);
        row[a.display_name] = (norm as Record<string, number>)[d.key];
      }
      return row;
    });
    return rows;
  }, [agents, selected, t]);

  const visibleAgents = agents.filter((a) => selected.has(a.slug));

  return (
    <div className="space-y-6">
      <Card>
        <h3 className="text-sm font-semibold mb-3">{t("compare_select_agents")}</h3>
        <div className="flex flex-wrap gap-2">
          {agents.map((a) => {
            const meta = metaFor(a.slug);
            const active = selected.has(a.slug);
            return (
              <button
                key={a.slug}
                onClick={() => {
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (next.has(a.slug)) next.delete(a.slug);
                    else next.add(a.slug);
                    return next;
                  });
                }}
                className={`px-3 py-1.5 rounded-full text-sm border transition-all ${
                  active
                    ? "border-council-700 dark:border-council-300 bg-council-50 dark:bg-council-800"
                    : "border-council-200 dark:border-council-700 text-muted"
                }`}
              >
                <span
                  className="inline-block w-2 h-2 rounded-full mr-2"
                  style={{ backgroundColor: meta?.color ?? "#999" }}
                />
                {a.display_name}
              </button>
            );
          })}
        </div>
      </Card>

      <Card>
        <h3 className="text-sm font-semibold mb-3">{t("compare_risk_return")}</h3>
        <div className="h-80 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={chartData}>
              <PolarGrid />
              <PolarAngleAxis dataKey="axis" tick={{ fontSize: 12 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
              {visibleAgents.map((a) => (
                <Radar
                  key={a.slug}
                  name={a.display_name}
                  dataKey={a.display_name}
                  stroke={metaFor(a.slug)?.color ?? "#999"}
                  fill={metaFor(a.slug)?.color ?? "#999"}
                  fillOpacity={0.18}
                  strokeWidth={2}
                />
              ))}
              <Tooltip />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        <p className="text-xs text-muted mt-2">
          {t("compare_axis_caption")}
        </p>
      </Card>

      <Card>
        <h3 className="text-sm font-semibold mb-3">{t("compare_metric_table")}</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
                <th className="py-2 pr-3">{t("col_agent")}</th>
                <th className="py-2 pr-3 text-right">{t("col_cagr")}</th>
                <th className="py-2 pr-3 text-right">{t("col_total_return")}</th>
                <th className="py-2 pr-3 text-right">{t("col_sharpe")}</th>
                <th className="py-2 pr-3 text-right">{t("col_sortino")}</th>
                <th className="py-2 pr-3 text-right">{t("col_calmar")}</th>
                <th className="py-2 pr-3 text-right">{t("col_max_dd")}</th>
                <th className="py-2 pl-3 text-right">{t("col_hit_rate")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleAgents.map((a) => {
                const meta = metaFor(a.slug);
                return (
                  <tr key={a.slug} className="border-b border-council-100 dark:border-council-800 last:border-b-0">
                    <td className="py-2 pr-3">
                      <span
                        className="inline-block w-2 h-2 rounded-full mr-2"
                        style={{ backgroundColor: meta?.color ?? "#999" }}
                      />
                      <span className="font-medium">{a.display_name}</span>
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <PctCell value={a.metrics.cagr_pct} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <PctCell value={a.metrics.total_return_pct} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <NumCell value={a.metrics.sharpe} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <NumCell value={a.metrics.sortino} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <NumCell value={a.metrics.calmar} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <PctCell value={a.metrics.max_drawdown_pct} />
                    </td>
                    <td className="py-2 pl-3 text-right">
                      <PctCell value={a.metrics.hit_rate_monthly_pct} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
