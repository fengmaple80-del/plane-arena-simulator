import { useEffect, useEffectEvent, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWebview, type DragDropEvent } from "@tauri-apps/api/webview"
import { ChevronDown, CircleAlert, Gamepad2, ImagePlus, ImageUp, LoaderCircle, Pencil, Play, Swords } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { chooseScreenshot, onProgress, readAccount, readAccounts, readCatalog, recognize, saveResultImage, simulate, type Progress, type Report, type Roster, type SavedResult, type Slot, type Source } from "./backend"
import { BatchTasks } from "./batch"
import { ScreenshotBatch, type ScreenshotBatchHandle } from "./screenshot-batch"
import { EditSlot, Formation, missing, positionName } from "./formation"
import { Outcome, ResultHistory, ResultImagePreview } from "./outcome"
import { accountInput, digestExport, renderResultImage, screenshotInput } from "./edition"
import "./arena.css"

const LABELS = { picking: "正在选择截图", recognizing: "正在识别阵容", reading: "正在读取阵容", simulating: "正在模拟", exporting: "正在生成结果图" }
type Stage = keyof typeof LABELS
const gamesIn = (roster: Roster) => [...new Set(roster.slots.map(s => s.team))].sort((a, b) => a - b)
const chosen = (roster: Roster, game: number | null) => roster.slots.filter(s => game === null || s.team === game)

const defaultSource: Source = screenshotInput ? "screenshot" : "manual"
const blankSlots = () => [1, 2, 3].flatMap(team => [1, 2].flatMap(camp =>
  Array.from({ length: 6 }, (_, position): Slot => ({
    team, camp, position, template_id: null, name: "", star: null, advancement: null,
  }))))

function Arena() {
  const [source, setSource] = useState<Source>(defaultSource)
  const [mode, setMode] = useState<"auto" | "review">("auto")
  const [batchImages, setBatchImages] = useState(false)
  const [activeImage, setActiveImage] = useState<number | null>(null)
  const screenshotQueue = useRef<ScreenshotBatchHandle>(null)
  const [serial, setSerial] = useState("127.0.0.1:16448")
  const [rank, setRank] = useState("32")
  const [samples, setSamples] = useState("300")
  const [scope, setScope] = useState<number | null>(null)
  const [viewed, setViewed] = useState(1)
  const [roster, setRoster] = useState<Roster | null>(null)
  const manualDraft = useRef<Roster | null>(null)
  const manualScope = useRef<number | null>(1)
  const [reports, setReports] = useState(() => new Map<number | null, Report>())
  const [history, setHistory] = useState<SavedResult[]>([])
  const [resultImage, setResultImage] = useState<{ path: string; url: string } | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [edit, setEdit] = useState<Slot | null>(null)
  const editTrigger = useRef<HTMLButtonElement>(null)
  const [stage, setStage] = useState<Stage | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState("")
  const [dragging, setDragging] = useState(false)
  const dropZone = useRef<HTMLDivElement>(null)
  const locked = useRef(false)
  const busy = stage !== null
  const report = reports.get(scope) ?? null
  const sampleCount = Number(samples)
  const validSamples = Number.isInteger(sampleCount) && sampleCount >= 1 && sampleCount <= 3000
  const rankNumber = Number(rank)
  const validRank = Number.isInteger(rankNumber) && rankNumber >= 1 && rankNumber <= 32
  const pending = roster ? chosen(roster, scope).filter(s => missing(s).length).length : 0
  const teams = roster ? gamesIn(roster) : []
  const teamPending = roster ? new Map(teams.map(game => [game, chosen(roster, game).filter(s => missing(s).length).length])) : new Map<number, number>()

  const onPaste = useEffectEvent((event: ClipboardEvent) => {
    if (!dropZone.current || (locked.current && !(batchImages && screenshotQueue.current?.isReading())) || edit || previewOpen) return
    const images = Array.from(event.clipboardData?.files ?? []).filter(file => file.type.startsWith("image/"))
    if (images.length === 0) return
    event.preventDefault()
    acceptImages(images, "粘贴的截图")
  })

  const onNativeDrop = useEffectEvent((event: DragDropEvent) => {
    const box = dropZone.current?.getBoundingClientRect()
    if (!box || event.type === "leave" || (locked.current && !(batchImages && screenshotQueue.current?.isReading())) || edit || previewOpen) { setDragging(false); return }
    const { x, y } = event.position.toLogical(window.devicePixelRatio)
    const inside = x >= box.left && x <= box.right && y >= box.top && y <= box.bottom
    setDragging(inside && event.type !== "drop")
    if (inside && event.type === "drop") acceptImages(event.paths)
  })

  useEffect(() => {
    const subscription = onProgress(event => setProgress(event))
    subscription.catch(console.warn)
    return () => { void subscription.then(unlisten => unlisten()).catch(() => undefined) }
  }, [])

  useEffect(() => {
    const paste = (event: ClipboardEvent) => onPaste(event)
    window.addEventListener("paste", paste)
    const subscription = isTauri() ? getCurrentWebview().onDragDropEvent(event => onNativeDrop(event.payload)) : null
    subscription?.catch(console.warn)
    return () => {
      window.removeEventListener("paste", paste)
      void subscription?.then(unlisten => unlisten()).catch(() => undefined)
    }
  }, [])

  useEffect(() => () => { if (resultImage) URL.revokeObjectURL(resultImage.url) }, [resultImage])

  function clearResultView() { setResultImage(null); setPreviewOpen(false); setProgress(null); setError("") }

  function clearResult() { setReports(new Map()); clearResultView() }

  function resetScreenshotSelection() {
    setActiveImage(null); setRoster(null); setScope(null); setViewed(1); clearResult()
  }

  function showRoster(value: Roster, result?: Report) {
    setRoster(value); setScope(null); setViewed(gamesIn(value)[0] ?? 1); clearResultView()
    setReports(result ? new Map([[null, result]]) : new Map())
  }

  function openManual() {
    setScope(manualScope.current); setViewed(manualScope.current ?? 1)
    if (manualDraft.current) { setRoster(manualDraft.current); return }
    void run(async () => {
      setStage("reading")
      const { heroes, dataset } = await readCatalog()
      if (!heroes.length) throw new Error("角色目录为空。")
      const draft: Roster = { source: "manual", players: ["左方", "右方"], label: dataset, slots: blankSlots(), heroes }
      manualDraft.current = draft
      setRoster(draft)
    })
  }

  const initializeManual = useEffectEvent(openManual)
  useEffect(() => { if (defaultSource === "manual") initializeManual() }, [])

  function changeSource(value: string) {
    if (locked.current || value === source) return
    if (source === "manual") manualScope.current = scope
    setSource(value as Source); setActiveImage(null); setRoster(null); setScope(null); setViewed(1); setDragging(false); clearResult()
    if (value === "manual") openManual()
  }

  async function run(work: () => Promise<unknown>) {
    if (locked.current) return
    locked.current = true
    setError("")
    try { await work() }
    catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { locked.current = false; setStage(null) }
  }

  function load(value: Roster) {
    const games = gamesIn(value)
    const next = scope !== null && games.includes(scope) ? scope : null
    setRoster(value); setScope(next); setViewed(next ?? games[0])
    return next
  }

  async function calculate(value: Roster, game: number | null) {
    clearResultView(); setStage("simulating")
    setReports(items => { const next = new Map(items); next.delete(game); return next })
    setProgress({ done: 0, total: sampleCount * (game === null ? gamesIn(value).length : 1) })
    const result = await simulate(value, game, sampleCount)
    setReports(items => new Map(items).set(game, result))
    if (result.games.length) setHistory(items => [...items, { id: items.length + 1, label: value.label, report: result, selected: true }])
    return result
  }

  function exportResult() {
    if (!roster || !report || edit) return
    void run(async () => {
      setStage("exporting")
      const png = await renderResultImage(report, roster)
      const path = await saveResultImage(png)
      setResultImage({ path, url: URL.createObjectURL(png) }); setPreviewOpen(true)
    })
  }

  function exportDigest() {
    const entries = history.filter(item => item.selected)
    const renderDigest = digestExport?.render
    if (!entries.length || edit || !renderDigest) return
    void run(async () => {
      setStage("exporting")
      const png = await renderDigest(entries)
      const path = await saveResultImage(png)
      setResultImage({ path, url: URL.createObjectURL(png) }); setPreviewOpen(true)
    })
  }

  async function loadScreenshot(image: string | File, label?: string) {
    setDragging(false); setRoster(null); clearResult(); setStage("recognizing")
    const result = await recognize(image, label)
    const next = load(result)
    if (scope !== null && next === null) { setError(`截图没有第 ${scope} 小局。`); return }
    if (mode === "auto" && validSamples && chosen(result, next).every(s => !missing(s).length)) {
      await calculate(result, next)
    }
  }

  function acceptImages(images: (string | File)[], label?: string) {
    if (edit || (locked.current && !(batchImages && screenshotQueue.current?.isReading()))) return
    if (batchImages) {
      if (!images.length) { setError("没有收到可读取的图片。"); return }
      setDragging(false)
      screenshotQueue.current?.add(images, label)
      return
    }
    if (images.length !== 1) {
      setError(images.length ? "一次只能导入一张截图。" : "没有收到可读取的图片。")
      return
    }
    void run(() => loadScreenshot(images[0], label))
  }

  function importScreenshot() {
    if (batchImages) {
      void (async () => {
        let selected: string[] = []
        await run(async () => {
          setStage("picking")
          const value = await chooseScreenshot(true)
          selected = typeof value === "string" ? [value] : value ?? []
        })
        if (selected.length) screenshotQueue.current?.add(selected)
      })()
      return
    }
    void run(async () => {
      setStage("picking")
      const path = await chooseScreenshot()
      if (typeof path !== "string" || !path) return
      await loadScreenshot(path)
    })
  }

  function importAccount() {
    void run(async () => {
      setRoster(null); clearResult(); setStage("reading")
      load(await readAccount(rankNumber, serial.trim()))
    })
  }

  function showGame(game: number) {
    if (locked.current || viewed === game) return
    setViewed(game)
    if (scope !== null) { setScope(game); clearResultView() }
  }

  function saveSlot(next: Slot) {
    if (!roster || !edit) return
    const slots = roster.slots.map(s => {
      if (s.team !== edit.team || s.camp !== edit.camp) return s
      if (s.position === edit.position) return next
      return s.position === next.position ? { ...s, position: edit.position } : s
    })
    const updated = { ...roster, slots }
    setRoster(updated); setEdit(null); clearResultView()
    setReports(items => { const next = new Map(items); next.delete(null); next.delete(edit.team); return next })
    if (source === "manual") manualDraft.current = updated
    if (batchImages && activeImage !== null) screenshotQueue.current?.update(activeImage, updated)
  }

  return <main className="arena-shell">
    <header className="masthead">
      <div className="brand-mark"><Swords aria-hidden="true" /></div>
      <div><div className="brand-name">古代战争</div><h1>位面胜负预判</h1></div>
      <Badge variant="outline" className="artifact-badge">神器技能 <b>10 级</b></Badge>
    </header>

    <section className="command-deck" aria-label="对局输入">
      <div className="source-row">
        <Tabs value={source} onValueChange={changeSource}>
          <TabsList aria-label="输入来源" className="source-tabs">
            {screenshotInput && <TabsTrigger value="screenshot" disabled={busy}><ImagePlus aria-hidden="true" />截图导入</TabsTrigger>}
            {accountInput && <TabsTrigger value="account" disabled={busy}><Gamepad2 aria-hidden="true" />账号读取</TabsTrigger>}
            <TabsTrigger value="manual" disabled={busy}><Pencil aria-hidden="true" />手动排阵</TabsTrigger>
          </TabsList>
        </Tabs>
        {source === "screenshot" ? <div className="source-options screenshot-options">
          <div className="segmented" aria-label="截图导入方式" role="group">
            <button type="button" aria-pressed={!batchImages} disabled={busy} onClick={() => { if (batchImages) { setBatchImages(false); resetScreenshotSelection() } }}>单张模拟</button>
            <button type="button" aria-pressed={batchImages} disabled={busy} onClick={() => { if (!batchImages) { setBatchImages(true); resetScreenshotSelection() } }}>批量收集</button>
          </div>
          {!batchImages && <div className="segmented" aria-label="截图确认方式" role="group">
            <button type="button" aria-pressed={mode === "auto"} onClick={() => setMode("auto")} disabled={busy}>自动算</button>
            <button type="button" aria-pressed={mode === "review"} onClick={() => setMode("review")} disabled={busy}>先核对</button>
          </div>}
          <div ref={dropZone} className="screenshot-drop" role="region" aria-label="拖入或粘贴截图"
            aria-busy={busy} data-dragging={dragging} onDragOver={event => {
              event.preventDefault()
              const allowed = (!locked.current || (batchImages && !!screenshotQueue.current?.isReading())) && !edit && event.dataTransfer.types.includes("Files")
              event.dataTransfer.dropEffect = allowed ? "copy" : "none"
              setDragging(allowed)
            }} onDragLeave={event => {
              if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false)
            }} onDrop={event => {
              event.preventDefault()
              setDragging(false)
              acceptImages(Array.from(event.dataTransfer.files))
            }}>
            <span className="screenshot-drop-label"><ImageUp aria-hidden="true" />拖入或粘贴截图</span>
            <Button variant="outline" onClick={importScreenshot} disabled={busy}><ImagePlus aria-hidden="true" />打开截图</Button>
          </div>
        </div> : source === "account" ? <div className="source-options account-fields">
          <div className="field compact-field"><Label htmlFor="serial">MuMu 地址</Label><Input id="serial" list="local-serials" value={serial} onChange={e => setSerial(e.target.value)} disabled={busy} />
            <datalist id="local-serials"><option value="127.0.0.1:16448" /><option value="127.0.0.1:16384" /></datalist></div>
          <div className="field compact-field"><Label htmlFor="rank">名次</Label><Input id="rank" type="number" min={1} max={32} value={rank} onChange={e => setRank(e.target.value)} disabled={busy}
            aria-invalid={!validRank || undefined} aria-describedby={!validRank ? "rank-error" : undefined} /></div>
          <Button variant="outline" onClick={importAccount} disabled={busy || !validRank || !serial.trim()}>读取双方阵容</Button>
          {!validRank && <p id="rank-error" className="field-pending" role="alert">名次请输入 1–32 的整数。</p>}
          {!serial.trim() && <p className="field-pending" role="alert">请输入 MuMu 地址。</p>}
        </div> : <div className="source-options manual-fields">
          <span>点阵容格选角色，填写星级和进阶。</span>
          {!roster && !busy && <Button variant="outline" onClick={openManual}>重读角色目录</Button>}
        </div>}
      </div>
      <div hidden={source !== "screenshot" || !batchImages}>
        <ScreenshotBatch ref={screenshotQueue} busy={busy} validSamples={validSamples} run={run}
          recognize={async (image, label) => { setStage("recognizing"); return recognize(image, label) }}
          calculate={value => calculate(value, null)}
          show={(id, value, result) => { setActiveImage(id); showRoster(value, result) }}
          onRemove={id => { if (id === activeImage || id === null) resetScreenshotSelection() }} />
      </div>
      {source === "account" && <BatchTasks busy={busy} validSamples={validSamples} run={run}
        read={async ranks => { setStage("reading"); return readAccounts(ranks, serial.trim()) }}
        calculate={value => calculate(value, null)} show={showRoster} />}
      <div className="setup-row">
        <div className="field compact-field"><Label htmlFor="scope">模拟范围</Label><div className="select-wrap">
          <select id="scope" value={scope ?? ""} disabled={busy} onChange={event => {
            const game = event.target.value ? Number(event.target.value) : null
            setScope(game); if (game !== null) setViewed(game); clearResultView()
          }}><option value="">{teams.length === 3 ? "整场（三局两胜）" : teams.length === 1 ? "可用单局" : "可用对局"}</option>
            {(roster ? teams : [1, 2, 3]).map(g => <option key={g} value={g}>第 {g} 小局</option>)}
          </select><ChevronDown aria-hidden="true" />
        </div></div>
        <div className="field compact-field"><Label htmlFor="samples">每局次数</Label><Input id="samples" type="number" min={1} max={3000} value={samples} disabled={busy} aria-invalid={!validSamples || undefined} aria-describedby={!validSamples ? "sample-error" : undefined} onChange={event => { setSamples(event.target.value); clearResult() }} /></div>
        <div className="process-state" role="status" aria-live="polite">
          {stage ? <><LoaderCircle className="spinner" aria-hidden="true" /><span>{LABELS[stage]}{stage === "simulating" && progress ? ` ${progress.done} / ${progress.total}` : ""}</span></>
            : !validSamples ? <span id="sample-error" className="field-pending">请输入 1–3000 的整数。</span>
            : report ? <span>模拟完成</span> : roster ? <span>{pending ? (source === "manual" ? `${pending} 个位置待排阵` : `${pending} 个位置待核对`) : "阵容就绪"}</span> : null}
        </div>
        <Button className="run-action" disabled={busy || !roster || (source !== "manual" && !!pending) || !validSamples} onClick={() => {
          if (source === "manual" && roster && pending) {
            const gap = chosen(roster, scope).filter(slot => missing(slot).length)
              .sort((a, b) => a.team - b.team || a.camp - b.camp || a.position - b.position)[0]
            setViewed(gap.team)
            setError(`第 ${gap.team} 小局 · ${roster.players[gap.camp - 1]}${positionName(gap.position)}缺少${missing(gap).join("、")}。`)
            return
          }
          if (roster) void run(async () => {
            const result = await calculate(roster, scope)
            if (batchImages && source === "screenshot" && activeImage !== null && scope === null) screenshotQueue.current?.setResult(activeImage, result)
          })
        }}><Play aria-hidden="true" />开始模拟</Button>
      </div>
      {stage === "simulating" && progress && <progress className="run-progress" value={progress.done} max={progress.total} aria-label="模拟进度" />}
    </section>

    {error && <div className="arena-alert" role="alert"><CircleAlert aria-hidden="true" /><span>{error}</span></div>}
    <div className="workspace">
      <section className="formation-area" aria-label="对阵阵容">
        <div className="formation-heading"><div><h2>对阵阵容</h2>{roster && <span title={roster.label}>{roster.label}</span>}</div>
          {teams.length ? <Tabs value={String(viewed)} onValueChange={value => showGame(Number(value))}>
            <TabsList aria-label="对阵小局" className="match-tabs">{teams.map(game => <TabsTrigger key={game} value={String(game)} disabled={busy}>第 {game} 局
              {!!teamPending.get(game) && <span className="pending-count" title={source === "manual" ? "待排阵位置" : "待核对位置"}>{teamPending.get(game)} {source === "manual" ? "待排阵" : "待核对"}</span>}
            </TabsTrigger>)}</TabsList>
          </Tabs> : null}</div>
        {source !== "manual" && pending > 0 && <div className="roster-alert" data-testid="roster-issue"><CircleAlert aria-hidden="true" />{pending} 个位置待核对</div>}
        {roster ? <Formation roster={roster} game={viewed} onEdit={(slot, trigger) => { editTrigger.current = trigger; setEdit(slot) }} busy={busy} />
          : <div className="empty-board"><Swords aria-hidden="true" /><span>{source === "manual" ? "尚未加载角色目录" : "未导入阵容"}</span></div>}
      </section>
      <Outcome report={report} viewed={viewed} showGame={showGame} status={stage === "simulating" ? "正在模拟" : "尚未生成结果"}
        onExport={!busy && roster && report?.games.length ? exportResult : undefined}
        onPreview={!busy && resultImage ? () => setPreviewOpen(true) : undefined} />
    </div>
    <ResultHistory entries={history} busy={busy} onExport={digestExport && exportDigest} exportLabel={digestExport?.label} onToggle={id =>
      setHistory(items => items.map(item => item.id === id ? { ...item, selected: !item.selected } : item))}
      onRename={(id, label) => setHistory(items => items.map(item => item.id === id ? { ...item, label } : item))} />
    {edit && roster && <EditSlot slot={edit} heroes={roster.heroes ?? []} manual={source === "manual"}
      onClear={() => saveSlot({ ...edit, template_id: null, name: "", star: null, advancement: null })}
      onClose={() => setEdit(null)} onRestoreFocus={() => editTrigger.current?.focus()} onSave={saveSlot} />}
    {resultImage && <ResultImagePreview image={resultImage} open={previewOpen} onOpenChange={setPreviewOpen} />}
  </main>
}

createRoot(document.getElementById("root")!).render(<Arena />)
