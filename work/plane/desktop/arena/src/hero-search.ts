import { pinyin } from "pinyin-pro"
import type { Hero } from "./backend"

// Match names rather than relying on variants of an ID from imported data.
const commonAliases: Record<string, string[]> = {
  "圣龙骑士": ["龙骑", "龙旗", "圣龙", "阿拉尼雅"],
  "圣子路西法": ["圣子"],
  "傲慢牧首路西法": ["傲慢"],
  "烬铠公主": ["公主", "烬铠", "莉奥拉"],
  "夺心魔之王维克": ["夺心魔"],
  "窃骰小丑": ["小丑", "窃骰"],
  "懒惰牧首": ["懒惰", "米迦勒"],
  "色欲牧首": ["色欲", "阿撒兹勒"],
  "贪婪牧首玛门": ["贪婪"],
  "瘟疫领主": ["瘟疫", "巴拉诺斯"],
  "血狮大帝": ["血狮", "阿修罗"],
  "元素之主": ["元素", "元主", "雷姆修斯"],
  "风暴之眼": ["莫得", "莫德", "风暴"],
  "元素之母": ["元母", "劳拉"],
  "琳妮之影": ["琳妮"],
  "琳妮之盾莱恩": ["琳妮"],
  "美杜莎": ["蛇女"],
  "圣盾骑士": ["圣盾"],
  "圣堂之王": ["圣堂"],
  "中庭之蛇耶梦加得": ["中庭之蛇"],
}

const normalize = (value: string) => value.normalize("NFKC").toLowerCase().replace(/[\s'-]/g, "")

export function indexHeroes(heroes: Hero[]) {
  const counts = new Map<string, number>()
  for (const hero of heroes) counts.set(hero.name, (counts.get(hero.name) ?? 0) + 1)
  return heroes.map(hero => {
    const full = hero.full_name ?? hero.name
    const names = new Set([hero.name, full, ...(commonAliases[hero.name] ?? []), ...(commonAliases[full] ?? [])])
    const keys = [...new Set([...names].flatMap(name => [name,
      pinyin(name, { toneType: "none", type: "array", v: true }).join(""),
      pinyin(name, { pattern: "first", toneType: "none", type: "array" }).join(""),
    ]).map(normalize))]
    const label = counts.get(hero.name) === 1 ? hero.name : (full === hero.name ? `${full} · ${hero.id}` : full)
    return { ...hero, label, keys }
  })
}

export function searchHeroes(heroes: ReturnType<typeof indexHeroes>, query: string, candidates: number[] = []) {
  const needle = normalize(query)
  const score = (keys: string[]) => !needle ? 0 : keys.includes(needle) ? 0
    : keys.some(key => key.startsWith(needle)) ? 1 : keys.some(key => key.includes(needle)) ? 2 : 3
  const priority = new Set(candidates)
  return heroes.map(hero => ({ hero, rank: score(hero.keys) })).filter(entry => entry.rank < 3)
    .sort((a, b) => a.rank - b.rank || Number(priority.has(b.hero.id)) - Number(priority.has(a.hero.id))
      || a.hero.label.localeCompare(b.hero.label, "zh-CN"))
    .map(entry => entry.hero)
}
