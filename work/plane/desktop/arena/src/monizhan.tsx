import { useEffect, useRef, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import { getCurrentWebview } from "@tauri-apps/api/webview"
import { Button } from "@/components/ui/button"
import { ImagePlus } from "lucide-react"
import { ManualMoni, MoniPortrait } from "./moni-manual"
import { encodeImage } from "./backend"

type Slot = {slot: string; hero_id: string; name: string; form: string}
type Result = {status: string; slots?: Slot[]; devices?: {id:string;name:string}[]; issues?: {slot: string; reason: string}[]; summary?: {samples: number; completed: number; counts: {left: number; right: number; draw: number; unknown: number}}}
const reasons: Record<string,string> = {cropped_slot:"头像被裁切", low_score:"头像不清晰", hero_margin:"角色难以区分", form_margin:"普通与觉醒形态难以区分", panel_missing:"缺少角色面板", panel_ambiguous:"存在多个面板", form_template_missing:"缺少形态模板"}
function position(slot: string) { return `${slot[0] === "L" ? "红方" : "蓝方"}${slot[1] === "F" ? "前排" : "后排"}第 ${slot.slice(2)} 位` }

export function Monizhan({active,onBusy}: {active: boolean;onBusy:(v:boolean)=>void}) {
  const input = useRef<HTMLInputElement>(null)
  const lock = useRef(false)
  const [busy, setBusy] = useState(false)
  const [label, setLabel] = useState("")
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState("")
  const [mode, setMode] = useState<"image"|"manual">("image")
  const [drag,setDrag] = useState(false)
  type NativeInput = {source:"clipboard"|"mumu"|"manual";device?:string;positions?:Record<string,string>}
  async function run(image: File | string | NativeInput) {
    if (lock.current) return
    lock.current = true; setBusy(true); onBusy(true); setResult(null); setError("")
    setLabel(typeof image === "string" ? image.split(/[\\/]/).pop() || "截图" : image instanceof File ? image.name || "粘贴的截图" : ({clipboard:"剪贴板截图",mumu:"MuMu 当前画面",manual:"手动阵容"})[image.source])
    try {
      if (image instanceof File && image.size > 12 * 1024 * 1024) throw Error("图片超过 12 MB。")
      const data = typeof image === "string" ? {path: image} : image instanceof File ? {image_base64: await encodeImage(image)} : image
      const res = await invoke<Result>("plane_request", {request: {action: "monizhan", ...data}})
      setResult(res)
      if (res.status === "not_monizhan") setError("请导入含红蓝双方 12 个站位的模拟战开战前完整截图。")
      else if (res.status === "unreadable") setError((res.issues || []).map(i => `${position(i.slot)}：${reasons[i.reason] || "请核对截图"}`).join("；") || "阵容识别不完整，请换一张清晰截图。")
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { lock.current = false; setBusy(false); onBusy(false) }
  }
  useEffect(() => {
    if (!active || mode !== "image") return
    let live = true
    const paste = (e: ClipboardEvent) => { const f = Array.from(e.clipboardData?.files || []).find(f => f.type.startsWith("image/")); if (f) { e.preventDefault(); void run(f) } else if (!e.clipboardData?.getData("text/plain")) {e.preventDefault(); void run({source:"clipboard"})} }
    document.addEventListener("paste",paste)
    const stop = getCurrentWebview().onDragDropEvent(e => {if (!live) return; setDrag(e.payload.type === "enter" || e.payload.type === "over"); if (e.payload.type === "drop" && e.payload.paths[0]) void run(e.payload.paths[0])})
    return () => {live = false; document.removeEventListener("paste",paste); void stop.then(fn=>fn()).catch(()=>undefined)}
  },[active,mode])
  const counts = result?.summary?.counts
  return <section hidden={!active} aria-label="模拟战预测" className="moni-panel">
    <div className="mode-switch" aria-label="模拟战输入方式">{([['image','截图 / MuMu'],['manual','自选阵容']] as const).map(([key,name])=><Button key={key} disabled={busy} aria-pressed={mode===key} variant={mode===key?'default':'outline'} onClick={()=>{if(mode!==key){setMode(key);setResult(null);setError('');setLabel('');setDrag(false)}}}>{name}</Button>)}</div>
    <div hidden={mode!=="image"}>
      <div className="moni-drop" tabIndex={0} data-drag={drag} role="group" aria-label="截图拖放区" onDragOver={e=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={e=>{e.preventDefault();setDrag(false);if(e.dataTransfer.files[0])void run(e.dataTransfer.files[0])}}>
        <ImagePlus size={30} aria-hidden="true"/><h2>把模拟战截图拖到这里</h2><p>也可以按 Ctrl+V 粘贴。请使用含双方 12 个站位的开战前完整截图。</p>
        <div className="moni-actions"><Button disabled={busy} onClick={()=>void run({source:"clipboard"})}>粘贴截图</Button><Button variant="outline" disabled={busy} onClick={()=>input.current?.click()}>选择模拟战截图</Button></div>
        <input hidden type="file" ref={input} aria-label="模拟战截图文件" accept="image/png,image/jpeg,image/webp" onChange={e=>{const f=e.target.files?.[0];e.target.value="";if(f)void run(f)}} />
      </div>
      <div className="moni-device"><div><h3>MuMu 阵容导入</h3><p>在模拟器里打开模拟战对阵页面，即可读取当前画面并计算。</p></div><Button disabled={busy} variant="outline" onClick={()=>void run({source:"mumu"})}>从 MuMu 读取</Button></div>
      {result?.devices?.map(d=><Button key={d.id} disabled={busy} onClick={()=>void run({source:"mumu",device:d.id})}>读取 {d.name}</Button>)}
    </div>
    <ManualMoni active={active && mode==="manual"} busy={busy} calculate={positions=>void run({source:"manual",positions})} edited={()=>{setResult(null);setError("");setLabel("")}}/>
    <span className="moni-status" role="status">{busy ? "正在识别阵容并模拟 10 场…" : label}</span>
    {error && <p role="alert" className="arena-alert">{error}</p>}
    {counts && result?.summary && <div className="moni-tally" aria-label="模拟战结果"><h2>模拟结果</h2><p>共 {result.summary.samples} 场 · 红方 {counts.left} 胜 · 蓝方 {counts.right} 胜 · 平局 {counts.draw} 场 · 未判 {counts.unknown} 场</p></div>}
    {!!result?.slots?.length && <div className="moni-teams">{["L","R"].map(camp=><section key={camp}><h3>{camp === "L" ? "红方" : "蓝方"}</h3>{result.slots?.filter(s=>s.slot[0]===camp).map(s=><p key={s.slot} className="moni-result-slot"><MoniPortrait hero={s.hero_id} awakened={s.form === "awakened"}/><small>{position(s.slot).slice(2)}</small> <b>{s.name}</b> · {s.form === "awakened" ? "觉醒" : "普通"}</p>)}</section>)}</div>}
  </section>
}
