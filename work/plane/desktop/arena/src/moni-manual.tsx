import { useEffect, useMemo, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { indexHeroes, searchHeroes } from "./hero-search"

type Form = {id:string; hero_id:string; name:string; awakened:boolean; panel:Record<string,number>}
export function MoniPortrait({hero, awakened}: {hero:string; awakened:boolean}) {
  const form = awakened ? "awakened" : "normal"
  return <img className="moni-portrait" src={`/moni-portraits/${hero}-${form}.webp`} alt="" width={48} height={48} />
}
export const spots = ["LF1","LF2","LF3","LB1","LB2","LB3","RF1","RF2","RF3","RB1","RB2","RB3"]
export const spotLabel = (id:string) => `${id[0] === "L" ? "红方" : "蓝方"}${id[1] === "F" ? "前排" : "后排"}第 ${id[2]} 位`
export function ManualMoni({active,busy,calculate,edited}: {active:boolean;busy:boolean;calculate:(positions:Record<string,string>)=>void;edited:()=>void}) {
  const [forms,setForms]=useState<Form[]>([])
  const [loadError,setLoadError]=useState("")
  const [attempt,retry]=useState(0)
  const [positions,setPositions]=useState<Record<string,string>>({})
  const [editing,setEditing]=useState("")
  const [query,setQuery]=useState("")
  const [candidate,select]=useState("")
  useEffect(()=>{
    if (!active || forms.length) return
    let live=true
    setLoadError("")
    invoke<Form[]>("plane_request",{request:{action:"monizhan",source:"forms"}}).then(rows=>{if(live)setForms(rows)}).catch(e=>{if(live)setLoadError(String(e))})
    return ()=>{live=false}
  },[active,attempt,forms.length])
  const index=useMemo(()=>indexHeroes(forms.map(f=>({id:Number(f.id),name:f.name,full_name:f.name}))),[forms])
  const byId=useMemo(()=>new Map(forms.map(f=>[f.id,f])),[forms])
  const rows=useMemo(()=>searchHeroes(index,query).map(row=>byId.get(String(row.id))!),[index,query,byId])
  const pick=byId.get(candidate)
  return <section hidden={!active} className="moni-manual"><h2>模拟战手动排阵</h2>
    <p>选择双方各六个站位。属性取自模拟战活动固定面板，普通与觉醒可分别选择。</p>
    {loadError ? <p role="alert">{loadError} <Button variant="outline" onClick={()=>retry(v=>v+1)}>重读活动数据</Button></p> : !forms.length && <p role="status">正在读取活动数据…</p>}
    <div className="moni-teams">{["L","R"].map(side=><section key={side}><h3>{side === "L"?"红方":"蓝方"}</h3><div className="moni-cards">{spots.filter(s=>s[0]===side).map(s=>{const f=byId.get(positions[s]);return <button key={s} disabled={busy||!forms.length} aria-label={spotLabel(s)} onClick={()=>{setEditing(s);setQuery("");select(positions[s]||"")}}><small>{spotLabel(s).slice(2)}</small>{f && <MoniPortrait hero={f.hero_id} awakened={f.awakened} />}<b>{f?f.name:"＋ 选择角色"}</b>{f&&<span>{f.awakened?"觉醒":"普通"}</span>}</button>})}</div></section>)}</div>
    <Button disabled={busy||Object.keys(positions).length!==12} onClick={()=>calculate(positions)}>开始模拟战</Button> <small>已排 {Object.keys(positions).length} / 12</small>
    <Dialog open={!!editing} onOpenChange={v=>{if(!v)setEditing("")}}><DialogContent className="edit-dialog moni-picker"><DialogHeader><DialogTitle>选择模拟战角色</DialogTitle><DialogDescription>{editing&&spotLabel(editing)}</DialogDescription></DialogHeader>
      <input autoFocus aria-label="搜索模拟战角色" placeholder="角色名、简称或拼音" value={query} onChange={e=>setQuery(e.target.value)} />
      <div className="moni-options" role="group" aria-label="活动角色与形态">
        {rows.map(f => <button type="button" key={f.id} data-form-id={f.id} aria-pressed={candidate === f.id} onClick={() => select(f.id)}>
          <MoniPortrait hero={f.hero_id} awakened={f.awakened} />
          <span><b>{f.name}</b><small>{f.awakened ? "觉醒" : "普通"}</small></span>
        </button>)}
      </div>
      {!rows.length && <small>没有匹配的活动角色。</small>}
      {pick&&<div className="moni-stats"><strong>{pick.name} · {pick.awakened?"觉醒":"普通"}</strong><div>{Object.entries(pick.panel).map(([key,v])=><span key={key}>{key} {v.toLocaleString()}</span>)}</div></div>}
      <Button disabled={!pick} onClick={()=>{setPositions(v=>({...v,[editing]:candidate}));setEditing("");edited()}}>确认站位</Button>
    </DialogContent></Dialog>
  </section>
}
