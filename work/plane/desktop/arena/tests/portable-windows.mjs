import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { readFile } from "node:fs/promises"
import { createServer } from "node:net"
import { dirname, resolve } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import { chromium, expect } from "@playwright/test"

const [exeArg, variant, fixture] = process.argv.slice(2)
const exe = resolve(exeArg)
const isPublic = variant !== "self"
const usesActualData = variant !== "demo"
const port = await new Promise((done, reject) => {
  const server = createServer().once("error", reject).listen(0, "127.0.0.1", () => {
    const value = server.address().port
    server.close(() => done(value))
  })
})
const system = process.env.SystemRoot ?? "C:\\Windows"
const verifyDir = process.env.PLANE_ARENA_QA_DIR
const env = {
  ...process.env,
  PLANE_ARENA_VERIFY_HIDDEN: "1",
  WEBVIEW2_USER_DATA_FOLDER: resolve(verifyDir, `arena-qa-webview-${process.pid}`),
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1 --disable-backgrounding-occluded-windows`,
}
if (isPublic) {
  env.PATH = `${system}\\System32;${system}`
  delete env.Path
  delete env.PYTHONPATH
  delete env.PYTHONHOME
  delete env.PLANE_ARENA_PYTHON
}
const child = spawn(exe, [], { env, cwd: dirname(exe), windowsHide: true, stdio: "ignore" })
const exited = new Promise(done => child.once("exit", done))
let startError
child.once("error", error => { startError = error })
let browser
try {
  const endpoint = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 25000
  for (;;) {
    if (startError) throw startError
    if (child.exitCode !== null) throw new Error(`Exited with ${child.exitCode}`)
    if ((await fetch(`${endpoint}/json/version`, { signal: AbortSignal.timeout(750) }).catch(() => null))?.ok) break
    if (Date.now() > deadline) throw new Error("WebView2 startup timed out")
    await sleep(125)
  }
  browser = await chromium.connectOverCDP(endpoint)
  const page = browser.contexts()[0].pages()[0]
  const errors = []
  page.on("pageerror", e => errors.push(String(e)))
  const begin = page.getByRole("button", { name: "开始模拟" })
  const board = page.getByRole("region", { name: "对阵阵容" })
  const outcome = page.getByTestId("results")
  const dialog = page.getByRole("dialog", { name: "选择角色" })
  const cards = board.getByTestId("fighter")
  const simulate = (slots, game, source = "manual", samples = 3) => invoke({ action: "simulate", source, slots, game, samples })
  const invoke = request => page.evaluate(request => window.__TAURI_INTERNALS__.invoke("plane_request", { request }), request)
  await expect(page.getByRole("heading", { name: "位面胜负预判" })).toBeVisible({ timeout: 15000 })
  if (!isPublic) await page.getByRole("tab", { name: "手动排阵" }).click()
  await expect(cards).toHaveCount(12, { timeout: 15000 })
  const catalog = await invoke({ action: "catalog" })
  assert.equal(catalog.dataset, usesActualData ? "内置位面角色" : "演示角色")
  assert.equal(catalog.heroes.length, 121)
  await page.getByRole("button", {name: "角色数据（121）"}).click()
  const directory = page.getByRole("dialog", {name: "角色数据"})
  const art = directory.locator("tbody img")
  await expect(art).toHaveCount(121)
  const assets = await art.evaluateAll(async imgs => Promise.all(imgs.map(async img => {
    await img.decode()
    if (!img.naturalWidth || img.naturalHeight !== img.naturalWidth) throw Error(img.src)
    return new URL(img.src).pathname.split("/").at(-1)
  })))
  assert.deepEqual(assets.sort(), catalog.heroes.map(h => `${h.id}.webp`).sort())
  await directory.getByLabel("检索角色数据").fill("龙骑")
  const dragon = directory.getByRole("row").filter({has: page.getByRole("rowheader", {name: "圣龙骑士", exact:true})})
  await expect(dragon).toBeVisible()
  const expected = catalog.heroes.find(hero => hero.name === "圣龙骑士").attributes
  for (const [i, key] of ["力量", "敏捷", "智力", "体质", "韧性"].entries()) {
    await expect(dragon.getByRole("cell").nth(i)).toHaveText(expected[key].split("|").join(" / "))
  }
  await page.keyboard.press("Escape")
  await page.getByLabel("每局次数").fill("3")
  await cards.first().getByRole("button").click()
  const query = dialog.getByLabel("搜索角色")
  for (const value of usesActualData ? ["龙骑", "圣龙骑士", "shenglongqishi", " SLQS "] : ["演练先锋", "yanlianxianfeng", "ylxf"]) {
    await query.fill(value)
    await expect(dialog.getByRole("option", { name: usesActualData ? "圣龙骑士" : "演练先锋", exact: true })).toBeVisible()
  }
  if (usesActualData) {
    await query.fill("莫得")
    await expect(dialog.getByRole("option", { name: "风暴之眼", exact: true })).toBeVisible()
  }
  await dialog.getByRole("button", { name: "取消" }).click()
  await begin.click()
  await expect(page.getByRole("alert")).toContainText("第 1 小局 · 左方前排 1")
  const slots = fixture ? JSON.parse(await readFile(fixture, "utf8")).units : Array.from({ length: 36 }, (_, i) => ({
    template_id: ["圣龙骑士", "安东尼", "风暴之眼", "圣子路西法", "懒惰牧首", "元素之主", "元素之母", "瘟疫领主", "血狮大帝", "色欲牧首", "夺心魔之王维克", "窃骰小丑"].map(name => catalog.heroes.find(h => h.name === name || h.full_name === name)?.id)[(i + Math.floor(i / 12)) % 12], team: Math.floor(i / 12) + 1,
    camp: i % 12 < 6 ? 1 : 2, position: i % 6, star: 6, advancement: (i % 6) % 2 ? 0 : 3,
  }))
  async function fillGame(game) {
    for (const slot of slots.filter(s => s.team === game)) {
      await cards.nth((slot.camp - 1) * 6 + slot.position).getByRole("button").click()
      await query.fill(catalog.heroes.find(hero => hero.id === slot.template_id).name)
      const option = dialog.getByRole("listbox").locator(`[id$="-${slot.template_id}"]`)
      await option.locator("img").evaluate(img => img.decode())
      await option.click()
      await dialog.getByLabel("星级").fill(String(slot.star))
      await dialog.getByLabel("进阶").selectOption(String(slot.advancement))
      await dialog.getByRole("button", { name: "确认" }).click()
    }
  }
  await fillGame(1)
  const formationImages = cards.locator(".portrait img")
  await expect(formationImages).toHaveCount(12)
  await formationImages.evaluateAll(imgs => Promise.all(imgs.map(img => img.decode())))
  await begin.click()
  await expect(outcome).toContainText("3 次", { timeout: 35000 })
  const one = await simulate(slots, 1)
  assert.equal(one.games.length, 1)
  assert.equal(one.games[0].unknown, 0)
  if (variant === "self") {
    assert.deepEqual(await simulate(slots, 1, "screenshot"), one)
  }
  if (slots.length === 36) {
    for (const game of [2, 3]) {
      await board.getByRole("tab", { name: new RegExp(`第 ${game} 局`) }).click()
      await fillGame(game)
    }
    await page.getByLabel("模拟范围").selectOption("")
    await begin.click()
    await expect(page.getByTestId("match-summary")).toBeVisible({ timeout: 35000 })
    await expect(page.getByTestId("match-summary").getByRole("button")).toHaveCount(3)
    const whole = await simulate(slots, null)
    assert.equal(whole.games.length, 3)
    assert.equal(whole.matches.length, 1)
    for (const game of whole.games) {
      assert.equal(game.samples, 3)
      assert.equal(game.unknown, 0)
      assert.deepEqual(game.errors, {})
    }
  } else {
    await begin.click()
    await expect(page.getByRole("status")).toContainText("模拟完成", { timeout: 35000 })
  }
  const history = page.getByRole("region", { name: "多场模拟结果" })
  await expect(history.locator(".history-row")).toHaveCount(2)
  if (isPublic) await expect(history.getByRole("button", { name: "导出一图流" })).toHaveCount(0)
  const exportButton = isPublic ? "生成结果图" : "导出一图流"
  await page.getByRole("button", { name: exportButton }).click()
  const preview = page.getByRole("dialog", { name: "结果图预览" })
  await expect(preview.getByRole("img")).toBeVisible({ timeout: 15000 })
  const artifact = await readFile((await preview.getByTestId("result-image-path").textContent()).trim())
  assert.equal(artifact.subarray(0, 8).toString("hex"), "89504e470d0a1a0a")
  if (isPublic) assert.equal(artifact.readUInt32BE(16), 960)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ variant, catalogSize: catalog.heroes.length, decodedPortraits: assets.length, singleGame: one.games[0], gamesInWholeMatch: slots.length / 12, history: 2, requestedExport: exportButton }))
} finally {
  await browser?.close().catch(() => undefined)
  if (child.exitCode === null && !startError) child.kill()
  await Promise.race([exited, sleep(3000)])
}
