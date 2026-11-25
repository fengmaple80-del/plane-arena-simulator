import { useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import { Check, ChevronDown, Pencil, Plus, Shield, Swords, UserRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { Hero, Roster, Slot } from "./backend"
import { indexHeroes, searchHeroes } from "./hero-search"
import { builtInPortraits } from "./edition"

export function missing(slot: Slot) {
  return [!slot.template_id && "角色", slot.star === null && "星级", slot.advancement === null && "进阶"].filter(Boolean)
}

export const positionName = (position: number) => `${position < 3 ? "前排" : "后排"} ${position % 3 + 1}`

export function portraitSources(slot: Slot, original = false) {
  if (original) return slot.portrait ? [slot.portrait] : []
  const recognized = slot.recognized
  const matchesPhoto = recognized && recognized.template_id === slot.template_id
    && recognized.star === slot.star && recognized.advancement === slot.advancement
  const asset = builtInPortraits && slot.template_id ? `${import.meta.env.BASE_URL}portraits/${slot.template_id}.webp` : null
  return [matchesPhoto ? slot.portrait : null, asset].filter((url): url is string => !!url)
}

function Portrait({ slot, original = false }: { slot: Slot; original?: boolean }) {
  const [failed, setFailed] = useState<string[]>([])
  const url = portraitSources(slot, original).find(source => !failed.includes(source))
  return url ? <img src={url} alt="" onError={() => setFailed(keys => [...keys, url])} /> : <UserRound aria-hidden="true" />
}

function Fighter({ slot, change, disabled, manual }: { slot: Slot; change?: (slot: Slot, trigger: HTMLButtonElement) => void; disabled: boolean; manual: boolean }) {
  const pending = missing(slot)
  const empty = manual && !slot.template_id
  return (
    <article className={`fighter${manual ? " fighter--manual" : ""}${pending.length ? " fighter--pending" : ""}${empty ? " fighter--empty" : ""}`} data-testid="fighter">
      {!empty && <>
        <div className="portrait"><Portrait slot={slot} /></div>
        <div className="fighter-copy">
          <div className="fighter-name" title={slot.name || "角色待确认"}>{slot.name || "角色待确认"}</div>
          <div className="fighter-meta">
            <span className={slot.star === null ? "field-pending" : ""}>{slot.star === null ? "星级?" : `${slot.star} 星`}</span>
            <span className={slot.advancement === null ? "field-pending" : ""}>{slot.advancement === null ? "进阶?" : `+${slot.advancement}`}</span>
          </div>
        </div>
      </>}
      {change && <button type="button" className={`fighter-edit${manual ? " fighter-edit--fill" : ""}`} disabled={disabled}
        name="edit-slot" title={pending.length ? (manual ? "添加角色" : "核对这张卡") : "修改这张卡"}
        aria-label={`${pending.length ? (manual ? "添加角色" : "核对") : "修改"}${slot.camp === 1 ? "左方" : "右方"}${positionName(slot.position)}${slot.name ? ` ${slot.name}` : ""}`}
        onClick={event => change(slot, event.currentTarget)}>{empty ? <><Plus aria-hidden="true" /><span>添加角色</span></> : <Pencil aria-hidden="true" />}</button>}
    </article>
  )
}

export function Formation({ roster, game, onEdit, busy }: {
  roster: Roster; game: number; onEdit: (slot: Slot, trigger: HTMLButtonElement) => void; busy: boolean
}) {
  return <div className="formation-grid" data-testid="board" aria-label={`第 ${game} 小局阵容`}>
    {([1, 2] as const).map(camp => {
      const rows = roster.slots.filter(s => s.team === game && s.camp === camp).sort((a, b) => a.position - b.position)
      return <section className={`team team--${camp}`} key={camp} aria-label={roster.players[camp - 1]}>
        <div className="team-title">
          {camp === 1 ? <Shield aria-hidden="true" /> : <Swords aria-hidden="true" />}
          <h3>{roster.players[camp - 1]}</h3>
          <span>{rows.filter(slot => slot.template_id).length} 人</span>
        </div>
        {["前排", "后排"].map((title, i) => <div className="rank-row" key={title}>
          <div className="rank-title"><span>{title}</span><span>{i === 0 ? "01–03" : "04–06"}</span></div>
          <div className="fighter-row">{rows.slice(i * 3, i * 3 + 3).map(s => <Fighter
            key={s.position} slot={s} manual={roster.source === "manual"} change={roster.source !== "account" ? onEdit : undefined} disabled={busy} />)}</div>
        </div>)}
      </section>
    })}
  </div>
}

export function EditSlot({ slot, heroes, manual = false, onClear, onClose, onRestoreFocus, onSave }: {
  slot: Slot; heroes: Hero[]; manual?: boolean; onClear?: () => void; onClose: () => void
  onRestoreFocus: () => void; onSave: (slot: Slot) => void
}) {
  const [hero, setHero] = useState(slot.template_id)
  const [star, setStar] = useState(slot.star?.toString() ?? (manual ? "6" : ""))
  const [advance, setAdvance] = useState(slot.advancement?.toString() ?? (manual ? "0" : ""))
  const [query, setQuery] = useState("")
  const [highlight, setHighlight] = useState<number | null>(null)
  const [position, setPosition] = useState(slot.position)
  const [error, setError] = useState<{ field: "hero" | "star" | "advance"; message: string }>()
  const originalAdvance = !manual && typeof slot.advancement === "number" && slot.advancement > 6 ? slot.advancement : null
  const listId = useId()
  const queryInput = useRef<HTMLInputElement>(null)
  const starInput = useRef<HTMLInputElement>(null)
  const advanceInput = useRef<HTMLSelectElement>(null)
  const heroIndex = useMemo(() => indexHeroes(heroes), [heroes])
  const sorted = useMemo(() => searchHeroes(heroIndex, query, slot.candidates), [heroIndex, query, slot.candidates])
  const selected = heroIndex.find(entry => entry.id === hero)
  const activeIndex = sorted.findIndex(entry => entry.id === (highlight ?? (query.trim() ? null : hero)))
  const active = sorted[Math.max(activeIndex, 0)]
  const optionId = (id: number) => `${listId}-${id}`

  function pick(id: number) {
    setHero(id)
    setHighlight(id)
    setError(undefined)
  }

  function handleSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === "Enter") {
      event.preventDefault()
      if (active) pick(active.id)
    } else if (sorted.length && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault()
      const index = Math.max(0, Math.min(sorted.length - 1, Math.max(activeIndex, 0) + (event.key === "ArrowDown" ? 1 : -1)))
      const next = sorted[index]
      setHighlight(next.id)
      document.getElementById(optionId(next.id))?.scrollIntoView({ block: "nearest" })
    }
  }

  function save(event: FormEvent) {
    event.preventDefault()
    const s = Number(star), a = Number(advance)
    if (!selected) {
      setError({ field: "hero", message: "请从搜索结果中选择一个角色。" }); queryInput.current?.focus(); return
    }
    if (!star || !Number.isInteger(s) || s < 1 || s > 6) {
      setError({ field: "star", message: "星级请输入 1–6 的整数。" }); starInput.current?.focus(); return
    }
    if (!advance || !Number.isInteger(a) || a < 0 || (a > 6 && a !== originalAdvance)) {
      setError({ field: "advance", message: "请从 +0 至 +6 选择进阶。" }); advanceInput.current?.focus(); return
    }
    onSave({ ...slot, name: selected.label, template_id: selected.id, star: s, advancement: a, position })
  }

  return <Dialog open onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent className="edit-dialog" onCloseAutoFocus={event => { event.preventDefault(); onRestoreFocus() }}>
      <DialogHeader>
        <DialogTitle>{manual ? "选择角色" : "核对阵容"}</DialogTitle>
        <DialogDescription>第 {slot.team} 小局 · {slot.camp === 1 ? "左方" : "右方"}{positionName(slot.position)}</DialogDescription>
      </DialogHeader>
      <form className="edit-form" onSubmit={save} noValidate>
        {slot.portrait && <div className="original-card"><Portrait slot={slot} original /><span>原图</span></div>}
        <div className="field"><Label htmlFor="hero-query">搜索角色</Label>
          <Input id="hero-query" ref={queryInput} role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls={listId}
            aria-activedescendant={active ? optionId(active.id) : undefined} aria-invalid={error?.field === "hero" || undefined}
            placeholder="名字、拼音、首字母或简称" value={query} onKeyDown={handleSearchKey}
            onChange={event => { setQuery(event.target.value); setHighlight(null); setError(undefined) }} />
          <div id={listId} role="listbox" aria-label="角色候选" className="hero-results">
            {sorted.map(entry => <div key={entry.id} id={optionId(entry.id)} role="option"
              aria-selected={entry.id === hero} data-highlighted={entry.id === active?.id}
              onMouseDown={event => event.preventDefault()} onClick={() => pick(entry.id)}>
              <span className="hero-name-with-icon">{builtInPortraits && <img alt="" src={`${import.meta.env.BASE_URL}portraits/${entry.id}.webp`} />} {entry.label}</span>{entry.id === hero && <Check aria-hidden="true" />}
            </div>)}
          </div>
          {!sorted.length && <p className="hero-empty">没有匹配的角色</p>}
          <p className="hero-selection" role="status">{selected ? <>已选 <b>{selected.label}</b></> : "点击候选角色，或用方向键选择后按回车。"}</p>
        </div>
        <div className="edit-numbers">
          <div className="field"><Label htmlFor="edit-star">星级</Label><Input id="edit-star" ref={starInput} type="number" min={1} max={6}
            aria-invalid={error?.field === "star" || undefined} value={star} onChange={event => { setStar(event.target.value); setError(undefined) }} /></div>
          <div className="field"><Label htmlFor="edit-adv">进阶</Label><div className="select-wrap">
            <select id="edit-adv" ref={advanceInput} value={advance} aria-invalid={error?.field === "advance" || undefined}
              onChange={event => { setAdvance(event.target.value); setError(undefined) }}>
              <option value="" disabled>选择进阶</option>
              {originalAdvance !== null && <option value={originalAdvance}>原数据 +{originalAdvance}</option>}
              {Array.from({ length: 7 }, (_, value) => <option key={value} value={value}>+{value}</option>)}
            </select><ChevronDown aria-hidden="true" />
          </div></div>
        </div>
        <div className="field"><Label htmlFor="edit-pos">站位</Label><div className="select-wrap">
          <select id="edit-pos" value={position} onChange={event => setPosition(Number(event.target.value))}>
            {Array.from({ length: 6 }, (_, i) => <option key={i} value={i}>{positionName(i)}</option>)}
          </select><ChevronDown aria-hidden="true" />
        </div></div>
        {error && <p className="field-pending" role="alert">{error.message}</p>}
        {manual && slot.template_id && <Button type="button" variant="ghost" onClick={onClear}>清空这个站位</Button>}
        <div className="edit-actions"><Button type="button" variant="outline" onClick={onClose}>取消</Button><Button type="submit">确认</Button></div>
      </form>
    </DialogContent>
  </Dialog>
}
