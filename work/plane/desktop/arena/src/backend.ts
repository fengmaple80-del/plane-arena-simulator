import { invoke } from "@tauri-apps/api/core"
import { listen } from "@tauri-apps/api/event"
import { open } from "@tauri-apps/plugin-dialog"

export type Source = "screenshot" | "account" | "manual"
export type Hero = { id: number; name: string; full_name?: string; attributes?: Record<string, string> }
export type Slot = {
  team: number; camp: number; position: number; template_id: number | null
  name: string; star: number | null; advancement: number | null
  portrait?: string; candidates?: number[]
  recognized?: Pick<Slot, "template_id" | "star" | "advancement">
}
export type Roster = {
  source: Source; label: string; players: [string, string]; slots: Slot[]
  snapshot?: unknown; heroes?: Hero[]
}
export type WinBounds = [number, number]
type Probability = { players: [string, string]; left_sample_bounds: WinBounds }
export type GameResult = Probability & {
  game: number; samples: number; left_wins: number; right_wins: number; unknown: number
  unjudged_reasons?: Record<string, number>
}
export type Scores = { samples: {seed:number; left:number|null; right:number|null}[]; completed:number; whole:boolean; averages:{left:number|null; right:number|null} }
export type Report = { scoring?: Scores; games: GameResult[]; matches: Probability[] }
export type SavedResult = { id: number; label: string; report: Report; selected: boolean }
export type Progress = { done: number; total: number }

export const chooseScreenshot = (multiple = false) => open({
  title: "打开双方阵容截图", directory: false, multiple,
  filters: [{ name: "截图", extensions: ["png", "jpg", "jpeg", "webp", "bmp"] }],
})

export async function encodeImage(file: File) {
  if (file.size > 20 * 1024 * 1024) throw new Error("图片超过 20 MB，请裁剪到双方阵容后重新导入。")
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error("无法读取图片，请重新导入。"))
    reader.onload = () => {
      if (typeof reader.result !== "string") { reject(new Error("没有收到可读取的图片。")); return }
      resolve(reader.result.slice(reader.result.indexOf(",") + 1))
    }
    reader.readAsDataURL(file)
  })
}

export async function recognize(image: string | File, label?: string) {
  const input = typeof image === "string" ? { path: image } : { image_base64: await encodeImage(image), label: label ?? image.name }
  const roster = await invoke<Roster>("plane_request", { request: { action: "recognize", ...input } })
  const slots = roster.slots.map(slot => ({ ...slot, recognized: {
    template_id: slot.template_id, star: slot.star, advancement: slot.advancement,
  } }))
  return { ...roster, slots }
}

export const readCatalog = () =>
  invoke<{ heroes: Hero[]; dataset: string }>("plane_request", { request: { action: "catalog" } })

export const readAccount = (rank: number, serial: string) =>
  invoke<Roster>("plane_request", { request: { action: "account", rank, serial } })

export type AccountEntry = { rank: number; roster?: Roster; error?: string }
export const readAccounts = (ranks: number[], serial: string) =>
  invoke<{ entries: AccountEntry[] }>("plane_request", { request: { action: "accounts", ranks, serial } })

export function simulate(roster: Roster, game: number | null, samples: number, scoring = false) {
  const input = roster.source === "account" ? { snapshot: roster.snapshot } : {
    slots: roster.slots.map(({ team, camp, position, template_id, star, advancement }) =>
      ({ team, camp, position, template_id, star, advancement })),
  }
  return invoke<Report>("plane_request", { request: { action: "simulate", source: roster.source, game, samples, scoring, ...input } })
}

export const onProgress = (callback: (value: Progress) => void) =>
  listen<Progress>("plane-progress", event => callback(event.payload))

export const saveResultImage = async (png: Blob) =>
  invoke<string>("save_result_image", await png.arrayBuffer())
