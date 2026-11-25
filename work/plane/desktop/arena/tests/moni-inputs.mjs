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
  const file = process.env.MONI_TEST_IMAGE
  const panel = page.getByRole("region", {name:"模拟战预测"})
  await page.getByRole("button",{name:"模拟战",exact:true}).click()
  await expect(panel).toBeVisible()
  const drop = panel.getByRole("group",{name:"截图拖放区"})
  await expect(drop).toBeVisible()
  await page.screenshot({path:resolve(verifyDir,"模拟战导入区.png")})
  const picture=await readFile(file)
  await drop.evaluate((el,b64)=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(b64), c=>c.charCodeAt(0))],"截图.png",{type:"image/png"}));el.dispatchEvent(new DragEvent("drop",{dataTransfer:dt,bubbles:true,cancelable:true}))},picture.toString("base64"))
  const tally = panel.getByLabel("模拟战结果")
  await expect(tally).toContainText("红方 8 胜 · 蓝方 2 胜",{timeout:90000})
  await expect(panel.locator(".moni-teams p b:visible")).toHaveCount(12)
  await page.getByRole("button",{name:"位面战",exact:true}).click()
  await expect(page.getByRole("heading",{name:"位面胜负预判"})).toBeVisible()
  await expect(page.getByRole("button",{name:"角色数据（121）"})).toBeVisible()
  await page.getByRole("button",{name:"模拟战",exact:true}).click()
  await expect(tally).toContainText("红方 8 胜")
  await panel.getByRole("button",{name:"自选阵容"}).click()
  const picks=JSON.parse(await readFile(resolve(verifyDir,"picks.json"),"utf8"))
  for (const [slot,id] of Object.entries(picks)) {
    const name=`${slot[0]==="L"?"红方":"蓝方"}${slot[1]==="F"?"前排":"后排"}第 ${slot[2]} 位`
    await panel.getByRole("button",{name,exact:true}).click()
    const dlg=page.getByRole("dialog",{name:"选择模拟战角色"});if(slot === "LF1") {
      const images=dlg.locator(".moni-options img")
      await expect(images).toHaveCount(187)
      assert.equal(await images.evaluateAll(async imgs=>{await Promise.all(imgs.map(img=>img.decode()));return new Set(imgs.map(img=>img.src)).size}),187)
      await dlg.getByLabel("搜索模拟战角色").fill("血狮")
      await expect(images).toHaveCount(2)
    }
    await dlg.locator(`[data-form-id="${id}"]`).click();
    if(slot === "LF1") await page.screenshot({path:resolve(verifyDir,"模拟战头像选人.png")});await expect(dlg.locator(".moni-stats")).toContainText("生命值");await dlg.getByRole("button",{name:"确认站位"}).click()
  }
  await expect(panel.locator(".moni-cards img")).toHaveCount(12);await panel.getByRole("button",{name:"开始模拟战"}).click();await expect(tally).toContainText("红方 8 胜 · 蓝方 2 胜",{timeout:90000})
  await page.screenshot({path:resolve(verifyDir,"模拟战手排结果.png")})
  await panel.getByRole("button",{name:"截图 / MuMu"}).click();await panel.getByRole("button",{name:"从 MuMu 读取"}).click();await expect(panel.getByRole("alert")).toContainText("启动 MuMu",{timeout:20000})
  const bytes = await readFile(process.env.MONI_WRONG_IMAGE)
  await page.evaluate(b64=>{
    const dt = new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(b64),c=>c.charCodeAt(0))],"位面截图.png",{type:"image/png"}));document.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true}))
  },bytes.toString("base64"))
  await expect(panel.getByRole("alert")).toContainText("模拟战开战前完整截图",{timeout:90000})
  await expect(tally).toHaveCount(0)
  const request = {action:"monizhan",path:process.env.MONI_PHONE_IMAGE};
  const res=await page.evaluate(request=>window.__TAURI_INTERNALS__.invoke("plane_request",{request}),request)
  assert.equal(res.status,"ok"); assert.equal(res.summary.completed,10); assert.equal(res.slots.length,12)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({first:"red 8 blue 2",second:res.summary.counts,wrongImage:"rejected",planeCatalog:121}))
} finally {
  await browser?.close().catch(() => undefined)
  if (child.exitCode === null && !startError) child.kill()
  await Promise.race([exited, sleep(3000)])
}
