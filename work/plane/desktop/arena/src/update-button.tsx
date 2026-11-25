import { useEffect, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import { Button } from "@/components/ui/button"

type Release = {current:string;latest:string;available:boolean}
export function UpdateButton({busy,lock}: {busy:boolean;lock:(v:boolean)=>void}) {
  const [release,setRelease]=useState<Release|null>(null)
  const [pending,setPending]=useState(false)
  const [message,setMessage]=useState("")
  useEffect(()=>{
    let live=true
    invoke<Release>("portable_update",{install:false}).then(r=>{if(live)setRelease(r)}).catch(()=>{})
    return ()=>{live=false}
  },[])
  async function check(){
    const install=release?.available===true
    if(install && !window.confirm(`更新到 ${release.latest} 并重新打开软件？请先保存需要的结果。`))return
    setPending(true);if(install)lock(true)
    setMessage(install?"正在下载并校验更新包…":"正在检查…")
    try {
      const r=await invoke<Release>("portable_update",{install}); setRelease(r)
      setMessage(r.available?(install?"正在打开新版…":"发现新版"): `已是最新版本 v${r.current}`)
    } catch(e){setMessage(`更新失败：${String(e)}`)}
    finally{setPending(false);if(install)lock(false)}
  }
  return <div className="app-update"><span role="status">{message}</span><Button variant="outline" size="sm" disabled={busy||pending} onClick={check}>{release?.available?`更新到 ${release.latest}`:"检查更新"}</Button></div>
}
