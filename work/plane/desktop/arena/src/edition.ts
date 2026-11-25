import type { Report, Roster, SavedResult, WinBounds } from "./backend"
import { otherSide, winRate } from "./result-presentation"

export const screenshotInput: boolean = false
export const accountInput: boolean = false
export const builtInPortraits: boolean = false
export const digestExport: { label: string; render: (entries: SavedResult[]) => Promise<Blob> } | undefined = undefined

export function renderResultImage(report: Report, roster: Roster): Promise<Blob> {
  const rows: [string, WinBounds][] = report.games.map(game => [`第 ${game.game} 小局 · ${game.samples} 次`, game.left_sample_bounds])
  if (report.matches[0]) rows.push(["整场 · 三局两胜", report.matches[0].left_sample_bounds])
  const canvas = document.createElement("canvas")
  canvas.width = 960
  canvas.height = 152 + rows.length * 62
  const ctx = canvas.getContext("2d")
  if (!ctx) return Promise.reject(new Error("无法生成结果图。"))
  ctx.fillStyle = "#ffffff"
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.fillStyle = "#18212b"
  ctx.font = 'bold 30px "Microsoft YaHei UI", sans-serif'
  ctx.fillText("模拟胜率", 36, 57)
  ctx.font = '20px "Microsoft YaHei UI", sans-serif'
  ctx.fillText(roster.players[0], 552, 111, 132)
  ctx.fillText(roster.players[1], 738, 111, 132)
  rows.forEach(([label, left], index) => {
    const y = 141 + index * 62
    ctx.fillStyle = index % 2 ? "#ffffff" : "#eef2f6"
    ctx.fillRect(24, y, 912, 56)
    ctx.fillStyle = "#18212b"
    ctx.fillText(label, 36, y + 35)
    ctx.fillText(winRate(left), 552, y + 35)
    ctx.fillText(winRate(otherSide(left)), 738, y + 35)
  })
  return new Promise((resolve, reject) => canvas.toBlob(blob => {
    if (blob) resolve(blob)
    else reject(new Error("无法生成结果图。"))
  }, "image/png"))
}
