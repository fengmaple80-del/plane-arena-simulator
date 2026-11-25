# 位面对战台

桌面工具，按双方阵容估计一小局或三局两胜整场的胜率。源码限非商业使用，详见 [LICENSE](LICENSE)。玩家可以免费使用、修改和分享，收费转卖、代算或提供付费服务需要另外取得授权。

## Windows 便携包

1. 解压全部文件到可写目录。双击 `位面对战台.exe`。需要 Windows x64 和系统的 Microsoft Edge WebView2 运行时。便携包自带 Python。
2. 点任意阵容格，用名字、全拼、拼音首字母或玩家简称搜索。选角色、星级和 `+0` 到 `+6` 的进阶，再点确认。每边填满六格可以模拟一小局。
3. 在对阵阵容上方切换第 2、3 局，补齐后在「模拟范围」选「整场（三局两胜）」。结果窗口显示各局和整场胜率。

首次启动自动载入 121 个位面角色。点「角色数据」可以查阅基础属性和每级成长。搜「龙骑」「圣龙骑士」「shenglongqishi」或「SLQS」均可找到圣龙骑士。点阵容格即可选人参加模拟。

角色目录随软件保存在 `data/plane-catalog.json`。旧版用户如有自己的 `data/local-catalog.json`，程序仍优先使用该文件。解压新版到新目录即可直接使用随包角色。

## 位面打分和模拟战

在「位面战」的「模拟模式」选「位面打分」，填写次数并开始模拟。结果区可展开每次的分数。三个小局全部完成后，整场计算会加入参与分和三局两胜奖励。均分仅统计完成结算的样本。

在「模拟战」点「自选阵容」，选择双方各六名角色及其普通或觉醒形态，再点「开始模拟战」。也可在「截图 / MuMu」粘贴或拖入一张包含双方十二个站位的开战前截图。头像随源码和便携包一起提供。游戏美术权利归相应权利人。

## v2.2.1 · 软件内更新

Windows 便携版启动时检查 GitHub 正式版本。点击窗口右上角的「更新到 v…」并确认后，程序下载并校验完整便携包，再打开新版。桌面快捷方式继续可用。更新前请保存本次需要的模拟结果。

使用 v2.2.0 或更早版本的用户，请先下载并完整解压一次 v2.2.1。此后的正式版本可以从界面更新。

## 从源码构建

需要 Node.js 22.12+、已配置 MSVC 与 Windows SDK 的 Rust，以及 Python 3.13。将 Python 放在 PATH，或用 `PLANE_ARENA_PYTHON` 指定本机 Python 的完整路径。

在 Visual Studio Developer PowerShell 中，切到本仓库并运行以下命令。

```powershell
Set-Location work/plane/desktop/arena
npm ci
npm run build
cargo build --locked --manifest-path src-tauri/Cargo.toml --release --features custom-protocol
Copy-Item -LiteralPath src-tauri/target/release/ancient-war-arena.exe -Destination ../../../../位面对战台.exe
Set-Location ../../../..
.\位面对战台.exe
```

第三方依赖版本可以在 `package-lock.json` 和 `Cargo.lock` 查阅。分发包随附 `第三方许可` 目录和 Python 的 `LICENSE.txt`。

从源码运行模拟战图片识别时，在所用 Python 环境安装 `numpy`、`opencv-python-headless` 和 `Pillow`，并将 `work/monizhan` 与 `work/plane` 加入 Python 模块搜索路径。Windows 便携包已包含这些依赖。
