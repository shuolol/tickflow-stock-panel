import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'

type Props = {
  isRunning: boolean
  onStart: (jobId: string) => void
}

/**
 * 除权因子 · 手动全量拉取面板。
 *
 * 在无 TickFlow Starter+(除权因子)权限时, 用户先在「设置 → 数据源」安装 baostock
 * 免费插件并把除权因子切换过去, 再回这里一键全量拉取(从上市至今, 补齐完整因子链),
 * 之后每日管道会自动增量补齐新除权事件。
 */
export function AdjFactorSyncPanel({ isRunning, onStart }: Props) {
  const qc = useQueryClient()
  const prefs = usePreferences()
  const sources = useQuery({ queryKey: QK.dataSources, queryFn: api.dataSources })

  // 解析当前实际除权因子来源 (same_as_daily → 跟随日K主源)
  const adjRaw = prefs.data?.adj_factor_provider
  const daily = prefs.data?.daily_data_provider || 'tickflow'
  const resolved = adjRaw === 'same_as_daily' ? daily : (adjRaw || 'tickflow')
  const isTickflow = resolved === 'tickflow'
  const adjName = isTickflow ? 'TickFlow'
    : (sources.data?.plugins?.find(s => s.name === resolved)?.display_name
       || sources.data?.custom?.find(s => s.name === resolved)?.display_name
       || resolved)

  const sync = useMutation({
    mutationFn: api.syncAdjFactor,
    onSuccess: ({ job_id }) => {
      onStart(job_id)
      qc.invalidateQueries({ queryKey: QK.pipelineJobs })
    },
  })

  return (
    <div className="px-4 pb-4 pt-3 border-t border-accent/20 space-y-4">
      <div className="space-y-2">
        <div className="text-xs font-medium text-foreground">除权因子数据源</div>
        <div className={`flex items-center gap-2 px-3 py-2 rounded-btn border ${
          isTickflow ? 'border-warning/20 bg-warning/10' : 'border-accent/20 bg-accent/5'
        }`}>
          <span className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${
            isTickflow ? 'bg-warning' : 'bg-accent'
          }`} />
          <span className="text-[11px] text-secondary">
            {isTickflow ? '当前: TickFlow (除权因子属 Starter+ 能力)' : `当前: ${adjName}`}
          </span>
        </div>
        {isTickflow && (
          <div className="px-3 py-1.5 rounded-btn bg-warning/10 border border-warning/20 text-[10px] text-warning leading-relaxed">
            你的套餐可能未开通 TickFlow 除权因子。请到
            <span className="font-medium"> 设置 → 数据源 </span>
            安装「baostock（免费除权因子）」插件并点「使用」切换, 再返回此处拉取。
          </div>
        )}
        <div className="text-[10px] text-muted leading-relaxed">
          全量拉取 A 股除权因子（从上市至今, 补齐完整因子链）, 并重算受影响标的的 Enriched
          前复权价格与技术指标。首次接入 baostock 等免费源后请执行一次；之后每日管道会自动增量补齐。
        </div>
      </div>

      <button
        onClick={() => sync.mutate()}
        disabled={isTickflow || isRunning || sync.isPending}
        className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent/90 text-base text-xs font-medium hover:bg-accent disabled:opacity-40 disabled:pointer-events-none transition-colors duration-150"
      >
        {sync.isPending ? (
          <><Loader2 className="h-3 w-3 animate-spin" />启动中…</>
        ) : (
          <>拉取除权因子</>
        )}
      </button>
      {sync.isError && (
        <div className="mt-2 text-[10px] text-danger">
          启动失败：{String((sync.error as Error)?.message ?? sync.error)}
        </div>
      )}
    </div>
  )
}
