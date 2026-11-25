import { useMemo, useState } from "react"
import type { Hero } from "./backend"
import { indexHeroes, searchHeroes } from "./hero-search"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"

export function HeroDirectory({ heroes }: { heroes: Hero[] }) {
  const [query, setQuery] = useState("")
  const indexed = useMemo(() => indexHeroes(heroes), [heroes])
  const rows = useMemo(() => searchHeroes(indexed, query), [indexed, query])
  return <Dialog>
    <DialogTrigger asChild><Button variant="outline">角色数据（{heroes.length}）</Button></DialogTrigger>
    <DialogContent className="edit-dialog hero-directory">
      <DialogHeader><DialogTitle>角色数据</DialogTitle><DialogDescription>每项属性依次显示基础值和每级成长。</DialogDescription></DialogHeader>
      <Input aria-label="检索角色数据" placeholder="角色名、简称或拼音" value={query} onChange={e => setQuery(e.target.value)} />
      <div className="hero-data-scroll"><table><thead><tr>{["角色", "力量", "敏捷", "智力", "体质", "韧性"].map(key => <th key={key} scope="col">{key}</th>)}</tr></thead>
        <tbody>{rows.map(hero => <tr key={hero.id}><th scope="row">{hero.label}</th>{["力量", "敏捷", "智力", "体质", "韧性"].map(key => <td key={key}>{hero.attributes?.[key]?.split("|").join(" / ") ?? "—"}</td>)}</tr>)}</tbody></table>
        {!rows.length && <p role="status">没有找到匹配的角色。</p>}
      </div>
    </DialogContent>
  </Dialog>
}
