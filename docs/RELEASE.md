MouseShare v0.7.7 · 文件直接接收与界面紧凑化 / Flat receive folder, denser UI

- 接收文件不再新建批次文件夹：文件与文件夹直接落到所选接收目录；重名自动追加「（1）」「（2）」，已有文件保持原样。
- 界面整体收紧：默认窗口 1060×820 → 880×700，侧栏 220 → 188，页面边距与区块间距同步收窄，屏幕布局画布 330 → 290。
- 修复侧栏「屏幕布局」被误写成「文件与剪贴板」：文件页刷新待接收角标时按旧序号覆盖了导航按钮文字。
- 修复单实例死锁：进程被强杀后残留的锁文件会让之后所有实例启动失败（提示「无法启动本机控制服务」），现在自动识别并回收。

Files now land directly in the chosen receive folder instead of a per-batch subfolder; duplicates take the next free 「（1）」/「（2）」 name. The window is more compact (880×700 default, narrower sidebar and tighter spacing). Fixes the sidebar label overwritten by the inbox counter and stale single-instance lock files that blocked every restart.

---

MouseShare v0.7.6 · 精简功能与瘦身 / Trimmed features, smaller packages

- 删除游戏模式：自动全屏／游戏进程检测、进程名单页面与 F12 快捷键一并移除；收回键鼠的 Esc 快捷键保持不变。
- 删除界面里的「使用说明」按钮，以及接收端的远端光标光圈（光标识别）。
- 界面固定为简体中文，删除英文文案与语言切换设置。
- 打包瘦身：启用 strip，并移除从未加载的 Qt 插件（触摸输入、QNetworkInformation、QSslSocket 后端、SVG 与部分图像格式）。
- 附带修复开机时可能同时启动两个实例的问题：改用原子 PID 锁文件判定唯一实例。

Removed game mode (automatic fullscreen/game-process detection, the process list page and its F12 shortcut); the Esc shortcut that reclaims input is unchanged. Removed the in-app help button and the remote pointer ring on the receiving side. The interface is now Simplified Chinese only; English strings and the language switch are gone. Packages are smaller after enabling strip and dropping Qt plug-ins that are never loaded. Also fixes a rare double launch at login by switching the single-instance check to an atomic PID lock file.

---

MouseShare v0.7.5 · 修复 Windows 碰边漏触发 / Fix missed Windows edge crossings

- 修复 Windows 返回后首次快速碰边被判为零移动、持续推动边缘也无法进入另一台电脑的问题。
- 本机控制时，使用 Raw Input 补充实际显示器边缘的向外移动；普通区域继续使用原有鼠标钩子，避免重复移动。
- 软件自身带标记的返回事件会更新坐标基准；连续零位移钩子事件不会覆盖原始鼠标的向外方向。
- 保留立即交接、正确返回位置、旧移动过滤、游戏模式及按住键鼠保护。可选本机诊断增加边缘原始移动和零位移钩子的计数，继续排除键值、指针轨迹及对端身份。

Windows now detects outward Raw Input at a physical display edge while local control is active. This fixes missed crossings after a return or when the cursor is already clamped to the edge. Tagged return events update the hook coordinate baseline, and repeated zero-displacement hook events no longer erase real outward motion. Regular local motion stays on the existing hook path.

Native callback regressions cover all four edges, immediate return/re-entry, peer recovery while staying at the edge, held-input/game protection, negative monitor coordinates, stale captured motion and tagged input filtering. These tests exercise the Windows callback entry points with simulated events; physical-device acceptance remains separate.

Preview packages: Windows x64 Setup/portable ZIP, macOS arm64/Intel DMGs, Linux x64/ARM64 DEB/portable archives. Linux input requires X11. No commercial code signature or Apple notarization. Verify SHA256SUMS.txt.
