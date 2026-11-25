import type { WinBounds } from "./backend"

export function winRate([low, high]: WinBounds) {
  const pct = (value: number) => (value * 100).toFixed(1)
  return Math.abs(low - high) < 1e-10 ? pct(low) + "%" : pct(low) + "–" + pct(high) + "%"
}

export const otherSide = ([low, high]: WinBounds): WinBounds => [1 - high, 1 - low]

export function bettingAdvice(players: [string, string], bounds: WinBounds, wholeMatch: boolean, minSamples: number) {
  const [low, high] = bounds
  const [rightLow] = otherSide(bounds)
  const lead = Math.max(low, rightLow)
  const camp = low > rightLow ? 1 : 2
  const player = players[camp - 1]
  const hasLead = lead > 0.5 + 1e-10
  if (!wholeMatch) return {
    title: "仅作单局参考",
    detail: hasLead ? player + "本局占优；押整场前请先模拟三局。" : "本局难分高下；押整场前请先模拟三局。",
  }
  if (minSamples < 50) return {
    title: "样本太少，建议观望",
    detail: "每小局至少模拟 50 次，再看整场押注倾向。",
  }
  if (lead >= 0.60 - 1e-10) return {
    title: (lead >= 0.80 - 1e-10 ? "优先考虑 " : "倾向 ") + player,
    detail: "整场模拟胜率下限 " + winRate([lead, lead]) + "。",
    camp,
  }
  return {
    title: "建议观望",
    detail: hasLead ? "优势方的整场模拟胜率下限不足 60%。" :
      high - low > 1e-10 ? "未判样本可能改变优势方。" : "双方的整场模拟胜率接近。",
  }
}
