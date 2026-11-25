import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { AccountEntry, Report, Roster } from "./backend"

type Entry = AccountEntry & { report?: Report; status: "ready" | "running" | "done" | "error" }

function parseRanks(text: string) {
  const ranks: number[] = []
  for (const part of text.trim().split(/[,，、;；\s]+/).filter(Boolean)) {
    const match = /^(\d+)(?:\s*[-~至—–]\s*(\d+))?$/.exec(part)
    if (!match) throw new Error("请填写名次或范围，例如 20-16 或 20，19，18。")
    const start = Number(match[1]), end = Number(match[2] ?? match[1])
    if (start < 1 || start > 32 || end < 1 || end > 32) throw new Error("名次应为 1 至 32。")
    const step = start <= end ? 1 : -1
    for (let rank = start; ; rank += step) {
      if (!ranks.includes(rank)) ranks.push(rank)
      if (rank === end) break
    }
  }
  if (!ranks.length) throw new Error("请填写要读取的名次。")
  return ranks
}

export function BatchTasks({ busy, validSamples, read, calculate, show, run }: {
  busy: boolean; validSamples: boolean
  read: (ranks: number[]) => Promise<{ entries: AccountEntry[] }>
  calculate: (roster: Roster) => Promise<Report>
  show: (roster: Roster, report?: Report) => void
  run: (work: () => Promise<void>) => Promise<void>
}) {
  const [text, setText] = useState("20-16")
  const [entries, setEntries] = useState<Entry[]>([])
  const [active, setActive] = useState(false)
  const [stopping, setStopping] = useState(false)
  const stop = useRef(false)
  let ranks: number[] = [], issue = ""
  try { ranks = parseRanks(text) } catch (error) { issue = (error as Error).message }
  const done = entries.filter(entry => entry.status === "done").length
  const failed = entries.filter(entry => entry.status === "error").length

  function update(rank: number, patch: Partial<Entry>) {
    setEntries(items => items.map(item => item.rank === rank ? { ...item, ...patch } : item))
  }

  function start(shouldRead: boolean, shouldSimulate: boolean) {
    void run(async () => {
      stop.current = false; setStopping(false); setActive(true)
      try {
        let queue = entries
        if (shouldRead) {
          setEntries([])
          const result = await read(ranks)
          queue = result.entries.map(entry => ({ ...entry, status: entry.roster ? "ready" : "error" }))
          setEntries(queue)
          const first = queue.find(entry => entry.roster)?.roster
          if (first) show(first)
        }
        if (shouldSimulate) {
          for (const entry of queue) {
            if (stop.current) break
            if (!entry.roster) continue
            update(entry.rank, { status: "running", error: undefined, report: undefined })
            show(entry.roster)
            try {
              const report = await calculate(entry.roster)
              update(entry.rank, { status: "done", report })
            } catch (error) {
              update(entry.rank, { status: "error", error: error instanceof Error ? error.message : String(error) })
            }
          }
        }
      } finally { setActive(false); setStopping(false) }
    })
  }

  return <section className="batch-tasks" aria-label="批量任务">
    <div className="batch-controls">
      <div className="field compact-field"><Label htmlFor="batch-ranks">批量名次</Label>
        <Input id="batch-ranks" value={text} onChange={event => setText(event.target.value)} disabled={busy}
          placeholder="20-16 或 20，19，18" aria-describedby="batch-help" /></div>
      <Button variant="outline" disabled={busy || !!issue} onClick={() => start(true, false)}>批量读取</Button>
      <Button variant="outline" disabled={busy || !validSamples || !entries.some(entry => entry.roster)}
        onClick={() => start(false, true)}>批量模拟</Button>
      <Button disabled={busy || !!issue || !validSamples} onClick={() => start(true, true)}>读取并模拟</Button>
      {active && <Button variant="outline" disabled={stopping} onClick={() => { stop.current = true; setStopping(true) }}>
        {stopping ? "本场结束后停止" : "停止后续任务"}</Button>}
    </div>
    <p id="batch-help" className="batch-help">{issue || "支持范围和多个名次；每场按整场三局计算，次数沿用下方设置，结果可在历史里查看。"}</p>
    {!!entries.length && <>
      <div className="batch-summary" role="status">共 {entries.length} 场 · 完成 {done} 场 · 异常 {failed} 场{active ? " · 正在处理" : ""}</div>
      <div className="batch-list">{entries.map(entry => <div className="batch-row" key={entry.rank} data-status={entry.status}>
        <span>第 {entry.rank} 名</span>
        <span className="batch-players">{entry.roster?.players.join(" 对 ") || "未读取"}</span>
        <span>{entry.error || ({ ready: "待模拟", running: "正在模拟", done: "已完成", error: "失败" })[entry.status]}</span>
        <Button size="sm" variant="ghost" disabled={busy || !entry.roster} onClick={() => show(entry.roster!, entry.report)}>查看阵容</Button>
      </div>)}</div>
    </>}
  </section>
}
