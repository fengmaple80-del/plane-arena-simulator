import { useImperativeHandle, useRef, useState, type Ref } from "react"
import { Button } from "@/components/ui/button"
import type { Report, Roster } from "./backend"
import { missing } from "./formation"

type ImageInput = string | File
type Entry = {
  id: number; label: string; selected: boolean; roster?: Roster; report?: Report; error?: string
  status: "queued" | "reading" | "ready" | "review" | "running" | "done" | "error"
}
export type ScreenshotBatchHandle = {
  isReading: () => boolean
  add: (images: ImageInput[], label?: string) => void
  update: (id: number, roster: Roster) => void
  setResult: (id: number, report: Report) => void
}
const pendingCount = (roster: Roster) => roster.slots.filter(slot => missing(slot).length).length

export function ScreenshotBatch({ ref, busy, validSamples, recognize, calculate, show, onRemove, run }: {
  ref: Ref<ScreenshotBatchHandle>; busy: boolean; validSamples: boolean
  recognize: (image: ImageInput, label: string) => Promise<Roster>
  calculate: (roster: Roster) => Promise<Report>
  show: (id: number, roster: Roster, report?: Report) => void
  onRemove: (id: number | null) => void
  run: (work: () => Promise<void>) => Promise<void>
}) {
  const [entries, setEntries] = useState<Entry[]>([])
  const items = useRef<Entry[]>([])
  const sequence = useRef(0)
  const queued = useRef<{ id: number; image: ImageInput; label: string }[]>([])
  const reading = useRef(false)
  const stop = useRef(false)
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  function commit(update: (entries: Entry[]) => Entry[]) {
    items.current = update(items.current); setEntries(items.current)
  }
  function update(id: number, patch: Partial<Entry>) {
    commit(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row))
  }
  function add(images: ImageInput[], label?: string) {
    const incoming = images.map(image => {
      const id = ++sequence.current
      const name = label || (typeof image === "string" ? image.split(/[\\/]/).pop() : image.name) || "截图"
      return { id, image, label: `${name} · ${id}` }
    })
    queued.current.push(...incoming)
    commit(rows => [...rows, ...incoming.map(({ id, label }): Entry => ({ id, label, selected: true, status: "queued" }))])
    if (reading.current) return
    reading.current = true
    void run(async () => {
      try {
        while (queued.current.length) {
          const entry = queued.current.shift()!
          update(entry.id, { status: "reading" })
          try {
            const roster = await recognize(entry.image, entry.label)
            roster.label = entry.label
            update(entry.id, { roster, status: pendingCount(roster) ? "review" : "ready" })
            show(entry.id, roster)
          } catch (error) {
            update(entry.id, { status: "error", error: error instanceof Error ? error.message : String(error) })
          }
        }
      } finally { reading.current = false }
    })
  }
  useImperativeHandle(ref, () => ({ add, isReading: () => reading.current,
    update: (id, roster) => update(id, { roster, report: undefined, error: undefined, status: pendingCount(roster) ? "review" : "ready" }),
    setResult: (id, report) => update(id, { report, status: "done", error: undefined }),
  }))

  const eligible = (entry: Entry) => entry.selected && entry.roster && !pendingCount(entry.roster) && entry.status !== "done"
  function simulateAll() {
    void run(async () => {
      stop.current = false; setStopping(false); setRunning(true)
      const ready = items.current.filter(eligible)
      try {
        for (const entry of ready) {
          if (stop.current) break
          update(entry.id, { status: "running", error: undefined })
          show(entry.id, entry.roster!)
          try {
            const report = await calculate(entry.roster!)
            update(entry.id, { report, status: "done" })
          } catch (error) {
            update(entry.id, { status: "error", error: error instanceof Error ? error.message : String(error) })
          }
        }
      } finally { setRunning(false); setStopping(false) }
    })
  }
  return <section className="batch-tasks screenshot-queue" aria-label="截图批量队列">
    <div className="batch-controls">
      <b>截图收集箱</b>
      <span className="batch-help">连续 Ctrl+V，可在识别时继续粘贴；也支持一次拖入或打开多张图。</span>
      <Button disabled={busy || !validSamples || !entries.some(eligible)} onClick={simulateAll}>批量模拟</Button>
      {running && <Button variant="outline" disabled={stopping} onClick={() => { stop.current = true; setStopping(true) }}>
        {stopping ? "本张结束后停止" : "停止后续任务"}</Button>}
      <Button variant="outline" disabled={busy || !entries.length} onClick={() => { commit(() => []); onRemove(null) }}>清空收集箱</Button>
    </div>
    <div className="batch-summary" role="status">已收集 {entries.length} 张 · 完成 {entries.filter(entry => entry.status === "done").length} 张</div>
    <div className="batch-list">{entries.map(entry => <div className="image-queue-row" key={entry.id} data-status={entry.status}>
      <input type="checkbox" checked={entry.selected} aria-label={`模拟截图 ${entry.id}`} disabled={busy}
        onChange={() => update(entry.id, { selected: !entry.selected })} />
      <span className="image-queue-label" title={entry.label}>{entry.label}</span>
      <span>{entry.error || (entry.status === "review" ? `${pendingCount(entry.roster!)} 个位置待核对` :
        ({ queued: "等待识别", reading: "正在识别", ready: "待模拟", running: "正在模拟", done: "已完成", error: "失败" } as Record<string, string>)[entry.status])}</span>
      <Button size="sm" variant="ghost" disabled={busy || !entry.roster}
        onClick={() => show(entry.id, entry.roster!, entry.report)}>查看 / 核对</Button>
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => { commit(rows => rows.filter(row => row.id !== entry.id)); onRemove(entry.id) }}>移除</Button>
    </div>)}</div>
  </section>
}
