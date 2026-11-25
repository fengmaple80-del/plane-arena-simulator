# 位面对战台

桌面工具，按双方阵容估计一小局或三局两胜整场的胜率。源码限非商业使用，详见 [LICENSE](LICENSE)。玩家可以免费使用、修改和分享，收费转卖、代算或提供付费服务需要另外取得授权。

## Windows 便携包

1. 解压全部文件到可写目录。双击 `位面对战台.exe`。需要 Windows x64 和系统的 Microsoft Edge WebView2 运行时。便携包自带 Python。
2. 点任意阵容格，用名字、全拼、拼音首字母或玩家简称搜索。选角色、星级和 `+0` 到 `+6` 的进阶，再点确认。每边填满六格可以模拟一小局。
3. 在对阵阵容上方切换第 2、3 局，补齐后在「模拟范围」选「整场（三局两胜）」。结果窗口显示各局和整场胜率。

首次启动使用内置原创演示数据。可以搜「演练先锋」和 `ylxf`。左方用演练先锋，右方用演练守卫，各填六格、默认六星 `+0`，就能跑一次示例。演示结果仅用于检查软件流程。

有可在本机使用的位面角色数据时，将数据文件命名为 `local-catalog.json` 并放到程序同级的 `data` 目录。重新启动后显示「本地位面角色」。例如搜索「圣龙骑士」「龙骑」「shenglongqishi」或「SLQS」都可找到圣龙骑士。内核使用这个本地数据文件计算。移走该文件后可切回演示数据。`data/demo-catalog.json` 展示了数据文件的结构。截图识别、账号读取需要原项目的资源和运行环境。

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
