MouseShare v0.7.10 · 自由摆放与一键同步系统排列 / Free placement, apply to Windows

- 屏幕布局不再回弹：拖动到哪里就停在哪里。此前只要两块屏有任何重叠（包括把自己电脑的第二屏拖过第一屏），保存前的校验会整体回滚，看起来就像被弹回去；现在重叠只作提示「有 N 处屏幕重叠，重叠的边缘不会跨屏」，位置一律保留。
- 边缘吸附保留：靠近邻屏边缘 12 单位以内自动贴齐，黄线出现即表示该处会把光标交给对方。
- 新增「应用到系统排列」（Windows）：两块屏之间的鼠标搬运是 Windows 自己的事，软件拦不住——所以要让软件的摆放真正生效，得让系统也认同这套几何。点一下按钮（或勾「保存时自动同步」），程序按画布摆放换算成本机像素坐标，通过 Display Configuration API 先试运行校验、再写回并重排显示器；主屏自动保持在 (0,0)。macOS 没有公开接口，会提示手动调整。
- 每块屏仍标注分辨率与系统坐标（含「主屏」），跨屏判定的依据一眼可见。

MouseShare v0.7.9 · 拖动跟手、相邻可见、远端不再拖尾 / Live drag, visible adjacency, no remote lag

- 屏幕布局拖动重做：画布不再用「抓手平移」抢走拖动（抓手只在没有屏幕的空白处生效），按住的屏幕**实时跟手**移动并带吸附预览，不再松手才跳位。
- 相邻关系可视化：两台电脑的屏幕贴上时画一条黄色高亮，一眼看出光标会从哪里交出去；拆开的每块屏还标注自己的分辨率、系统坐标和主屏标记，摆错对象一目了然。
- 接收端运动合成：快速晃动／框选时，把积压的移动合并成每 4ms 一次注入。总位移不变（终点一致），但「手停了窗口还在晃、框选自己长大」的拖尾消失。
- 跨屏调试日志搬到配置目录：在配置目录放一个空的 `.edge-debug` 文件即可记录每次跨界判定和生效的屏幕摆放（Windows `%LOCALAPPDATA%\MouseShare\`，macOS `~/Library/Application Support/MouseShare/`）。

Layout dragging now tracks the cursor live (the canvas hand-drag only engages on empty space), shows a yellow stripe wherever two computers' screens actually touch, and labels each block with its resolution, system position and primary flag. The receiving side sums arriving motion and injects once per 4 ms — same total travel, no rubber-band that keeps growing after the hand stops. Edge decisions can be logged by dropping a `.edge-debug` file next to the settings.

---

MouseShare v0.7.8 · 屏幕布局可逐块拖动 / Per-monitor layout placement

- 屏幕布局改为以「显示器」为单位：双屏电脑不再被绑成一个整体，每块屏可以独立拖到画布任意位置（默认仍按系统几何排列）。
- 跨屏判定与文件投递入口改用每块屏的实际摆放矩形：把第二屏拖到另一台电脑旁边，就能和那台电脑跨屏，而不只是和它自己的另一块屏。
- 摆放结果写入布局的可选 `monitors` 字段；旧版本读到会忽略，与老配置双向兼容。显示器数量变化（拔插、改分辨率排列）时丢弃失效摆放，未保存的草稿照旧保留。
- 保存时按每块屏的实际矩形做重叠校验，压到别的屏上会被拒绝并回退。

The layout unit is now the physical monitor: a multi-monitor computer is no longer one rigid block — drag each display independently (system geometry remains the default). Crossing targets and drop entries are computed from each display's actual placement, so a split-off screen can neighbour another computer. Placements live in an optional `monitors` field that older versions simply ignore.

---

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
