import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, FileJson, History, RefreshCw } from 'lucide-react'
import {
  api,
  type BacktestResultKind,
  type BacktestResultSummary,
  type StrategyBacktestResult,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { fmtPct, fmtPrice, priceColorClass } from '@/lib/format'
import { EmptyState } from '@/components/EmptyState'
import { StrategyNavChart } from './charts/StrategyNavChart'

// A股语义色:红涨绿跌 (与全项目一致)。收益/胜率等用 priceColorClass 取色。
const KIND_META: Record<BacktestResultKind, { text: string; cls: string }> = {
  backtest: { text: '策略回测', cls: 'border-blue-400/30 bg-blue-400/10 text-blue-400' },
  optimize: { text: '参数优化', cls: 'border-amber-400/30 bg-amber-400/10 text-amber-400' },
  walkforward: { text: '步进优化', cls: 'border-purple-400/30 bg-purple-400/10 text-purple-400' },
  unknown: { text: '其他', cls: 'border-border bg-surface text-muted' },
}

function fmtTime(t: number | undefined | null): string {
  if (!t) return '—'
  return new Date(t * 1000).toLocaleString('zh-CN', { hour12: false })
}

function isBacktestResult(r: unknown): r is StrategyBacktestResult {
  return (
    !!r &&
    typeof r === 'object' &&
    Array.isArray((r as Record<string, unknown>).equity_curve) &&
    (r as Record<string, unknown>).config != null
  )
}

function Metric({ label, value, colorCls }: { label: string; value: string; colorCls?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface/60 px-3 py-2">
      <div className="text-[11px] text-secondary">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${colorCls ?? 'text-foreground'}`}>{value}</div>
    </div>
  )
}

function StatGrid({ stats }: { stats: Record<string, any> }) {
  const num = (k: string) => (stats[k] == null || Number.isNaN(Number(stats[k])) ? '—' : String(stats[k]))
  const pct = (k: string) => fmtPct(stats[k] as number | null | undefined)
  const metrics: { label: string; value: string; colorCls?: string }[] = [
    { label: '累计收益', value: pct('total_return'), colorCls: priceColorClass(stats.total_return as number) },
    { label: '平均收益', value: pct('avg_return'), colorCls: priceColorClass(stats.avg_return as number) },
    { label: '中位数', value: pct('median_return'), colorCls: priceColorClass(stats.median_return as number) },
    { label: '胜率', value: pct('win_rate'), colorCls: priceColorClass(stats.win_rate as number) },
    { label: '盈亏比', value: stats.profit_factor != null ? Number(stats.profit_factor).toFixed(2) : '—' },
    { label: '超额(vs基准)', value: pct('excess'), colorCls: priceColorClass(stats.excess as number) },
    { label: '夏普', value: stats.sharpe != null ? Number(stats.sharpe).toFixed(2) : '—' },
    { label: '最大回撤', value: pct('max_drawdown'), colorCls: priceColorClass(stats.max_drawdown as number) },
  ]
  const info: { label: string; value: string }[] = [
    { label: '候选样本', value: num('n_candidates') },
    { label: '信号天数', value: num('n_days') },
    { label: '日均候选', value: num('avg_daily_candidates') },
    { label: '最佳', value: pct('best') },
    { label: '最差', value: pct('worst') },
    { label: '基准(上证)', value: pct('benchmark_return') },
    { label: '交易数', value: num('n_trades') },
  ]
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {metrics.map((m) => (
          <Metric key={m.label} {...m} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-secondary">
        {info.map((i) => (
          <span key={i.label}>
            {i.label} <b className="text-foreground tabular-nums">{i.value}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

function TradesTable({ trades }: { trades: StrategyBacktestResult['trades'] }) {
  if (!trades?.length) return null
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-xs">
        <thead className="bg-surface/80 text-secondary">
          <tr>
            <th className="px-3 py-2 text-left font-medium">代码</th>
            <th className="px-3 py-2 text-left font-medium">买入 → 卖出</th>
            <th className="px-3 py-2 text-right font-medium">入/出价格</th>
            <th className="px-3 py-2 text-right font-medium">收益</th>
            <th className="px-3 py-2 text-right font-medium">持有</th>
            <th className="px-3 py-2 text-left font-medium">出场原因</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-t border-border/60 hover:bg-elevated/40">
              <td className="px-3 py-1.5 tabular-nums">{t.symbol}</td>
              <td className="px-3 py-1.5 tabular-nums">
                {t.entry_date} → {t.exit_date}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {fmtPrice(t.entry_price)} / {fmtPrice(t.exit_price)}
              </td>
              <td className={`px-3 py-1.5 text-right font-semibold tabular-nums ${priceColorClass(t.pnl_pct)}`}>
                {fmtPct(t.pnl_pct)}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{t.duration}天</td>
              <td className="px-3 py-1.5">{t.exit_reason || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RawJson({ data }: { data: unknown }) {
  return (
    <div className="relative rounded-lg border border-border bg-surface/40">
      <pre className="max-h-[420px] overflow-auto p-3 text-[11px] leading-relaxed text-secondary">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  )
}

export function BacktestHistory() {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: QK.backtestResults,
    queryFn: api.backtestHistoryList,
  })
  const [selected, setSelected] = useState<BacktestResultSummary | null>(null)

  const detail = useQuery({
    queryKey: QK.backtestResult(selected?.name ?? null),
    queryFn: () => api.backtestHistoryGet(selected!.name),
    enabled: !!selected,
  })

  const results = data?.results ?? []

  // ── 详情视图 ──────────────────────────────────────────────
  if (selected) {
    const detailData = detail.data
    // 类型守卫: 只有确实是策略回测类结果才按 StrategyBacktestResult 渲染, 否则落到原始 JSON 视图
    const detailOk = detailData && isBacktestResult(detailData) ? detailData : null
    const stats = detailOk?.stats ?? {}
    const cfg = selected.config ?? {}
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setSelected(null)}
            className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface/60 px-2.5 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-elevated hover:text-foreground cursor-pointer"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> 返回列表
          </button>
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium text-foreground">{selected.strategy_id ?? selected.name}</span>
            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${KIND_META[selected.kind].cls}`}>
              {KIND_META[selected.kind].text}
            </span>
          </div>
          <span className="ml-auto text-xs text-secondary">{fmtTime(selected.saved_at)}</span>
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-secondary">
          <span>区间 <b className="text-foreground tabular-nums">{cfg.start} ~ {cfg.end}</b></span>
          {cfg.mode && <span>模式 <b className="text-foreground">{cfg.mode}</b></span>}
          {Array.isArray(cfg.symbols) && (
            <span>标的 <b className="text-foreground tabular-nums">{cfg.symbols.length} 只</b></span>
          )}
        </div>

        {detail.isLoading ? (
          <div className="grid flex-1 place-items-center text-xs text-secondary">加载存档…</div>
        ) : detail.isError ? (
          <EmptyState icon={FileJson} title="存档读取失败" hint="文件可能已损坏或被移动，请刷新重试。" />
        ) : detailOk ? (
          <div className="min-h-0 flex-1 overflow-y-auto space-y-4">
            <StatGrid stats={stats} />
            {detailOk.equity_curve?.length ? (
              <div className="rounded-lg border border-border bg-surface/40 p-3">
                <div className="mb-2 text-xs font-medium text-secondary">净值 / 回撤</div>
                <StrategyNavChart result={detailOk} />
              </div>
            ) : null}
            <div>
              <div className="mb-1 text-xs font-medium text-secondary">交易明细 ({detailOk.trades?.length ?? 0})</div>
              <TradesTable trades={detailOk.trades ?? []} />
            </div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
            <div className="text-xs text-secondary">该存档为{ KIND_META[selected.kind].text }结果，结构不同于策略回测，输出其原文：</div>
            <RawJson data={detailData} />
          </div>
        )}
      </div>
    )
  }

  // ── 列表视图 ──────────────────────────────────────────────
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium text-foreground">历史回测存档</h2>
        <span className="text-xs text-secondary">({data?.count ?? 0})</span>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface/60 px-2.5 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-elevated hover:text-foreground disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {isLoading ? (
        <div className="grid flex-1 place-items-center text-xs text-secondary">加载中…</div>
      ) : isError ? (
        <EmptyState icon={History} title="历史存档加载失败" hint="请确认后端已重启且可访问。" />
      ) : results.length === 0 ? (
        <EmptyState
          icon={History}
          title="暂无历史回测存档"
          hint="运行一次「策略回测」后，结果会自动保存到本地（data/backtest_results/），即可在此查看与对比。"
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface/90 text-secondary backdrop-blur">
              <tr>
                <th className="px-3 py-2 text-left font-medium">保存时间</th>
                <th className="px-3 py-2 text-left font-medium">策略</th>
                <th className="px-3 py-2 text-left font-medium">类型</th>
                <th className="px-3 py-2 text-left font-medium">区间</th>
                <th className="px-3 py-2 text-right font-medium">累计收益</th>
                <th className="px-3 py-2 text-right font-medium">夏普</th>
                <th className="px-3 py-2 text-right font-medium">胜率</th>
                <th className="px-3 py-2 text-right font-medium">交易数</th>
                <th className="px-3 py-2 text-right font-medium">最大回撤</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const m = r.metrics
                return (
                  <tr
                    key={r.name}
                    onClick={() => setSelected(r)}
                    className="cursor-pointer border-t border-border/60 hover:bg-elevated/40"
                  >
                    <td className="px-3 py-2 tabular-nums text-secondary">{fmtTime(r.saved_at)}</td>
                    <td className="px-3 py-2 font-medium text-foreground">{r.strategy_id ?? '—'}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${KIND_META[r.kind].cls}`}>
                        {KIND_META[r.kind].text}
                      </span>
                    </td>
                    <td className="px-3 py-2 tabular-nums text-secondary">
                      {r.config.start} ~ {r.config.end}
                    </td>
                    <td className={`px-3 py-2 text-right font-semibold tabular-nums ${priceColorClass(m.total_return as number)}`}>
                      {fmtPct(m.total_return as number | null)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {m.sharpe != null ? Number(m.sharpe).toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {m.win_rate != null ? fmtPct(m.win_rate as number) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-secondary">{m.n_trades ?? '—'}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${priceColorClass(m.max_drawdown as number)}`}>
                      {fmtPct(m.max_drawdown as number | null)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
