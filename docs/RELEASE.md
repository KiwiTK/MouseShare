MouseShare v0.8.31 · 第十五层：预登录配置路径盘符相对化修复 + 双实例修复（Windows） / drive-relative config path & dual-instance fixes

- 修复预登录跨屏不生效的根因：`Path(SystemDrive) / "Users"` 会生成盘符相对路径 `C:Users\...`（缺根分隔符，解析到服务工作目录下），配置目录校验恒败，预登录 GUI 从未被拉起（0.8.30 日志可见 `config incomplete in C:Users\Administrator\...`）。改用 `Path(SystemDrive).anchor`（`C:\`）构造绝对路径，last-user 主路径与回退扫描一并修复。
- 修复副机出现两个 MouseShare 窗口（一已连接一未连接）的两条路径：①登录瞬间服务拉起 GUI 与开机自启动项赛跑，输家等待超时后会删除仍存活的锁强行启动第二实例——现在只要锁主进程存活就一直等待，绝不删活锁；②进程存活检测遇「拒绝访问」（提权运行的锁主）误判为已死亡——现在视为存活。
- 服务会话中残留的 SYSTEM 身份预登录 GUI（服务重启后句柄丢失的孤儿）在用户登录后会被自动终结，保证登录桌面只有一个 GUI。
- 修复对刚自行退出的注入助手执行 TerminateProcess 报 ERROR_ACCESS_DENIED 的竞态：收割死进程提前到用户切换处理之前，终止操作失败仅记录不再中断循环。
- 兼容性说明：不同应用版本之间本可互通（发现与会话校验用的是协议版本常量，非应用版本），升级无需三台机器同步。
- v0.8.30（第十三层）：预登录 GUI 跳过原因写入日志（不再无声放弃）+ last-user 配置目录失效时扫描 C:\Users\*\AppData\Local\MouseShare 回退 + supervisor 轮询 10 秒→2 秒。
- v0.8.29（第十二层修复）：服务拉起 GUI 恒报 [WinError 87]——CreateEnvironmentBlock 返回 Unicode 环境块，CreateProcessAsUserW 必须带 CREATE_UNICODE_ENVIRONMENT 标志；补上后服务拉起首次真正生效（日志出现 app launched）。
- v0.8.28（第十一层，登录前跨屏）：服务记录最近登录用户名（last_user.txt），无人登录时以 SYSTEM 身份把完整 GUI 拉到控制台会话并经 --config-dir 指向真实用户配置目录（身份、配对、锁屏设置全部复用），登录瞬间交棒（先杀后拉）；支持 MOUSESHARE_CONFIG_DIR 与 --config-dir 覆盖；预登录目录校验 settings.json + identity.key 且拒绝路径穿越。
- v0.8.27：修复注入助手一连上就崩溃退出的死循环（injector spawned → app connected → 数秒后 injector exited；反复重拉）：pywin32 的 win32pipe 模块只有管道管理函数（Create/Connect/Disconnect），ReadFile/WriteFile/CloseHandle 在 win32file——此前第一次读管道即抛 AttributeError，输入记录从未被读走，锁屏注入始终收不到事件。
- 同步修正 win32file.ReadFile 的 (hr, data) 元组返回（hr 仅接受 0 与 ERROR_MORE_DATA），并替换全部 3 处管道句柄关闭调用。
- 新增静态 guard 测试禁止在管道场景误用 win32pipe.ReadFile/WriteFile/CloseHandle。
- v0.8.26：修复服务拿不到注入助手子进程句柄（PROCESS_INFORMATION.hProcess 结构体字段取出即为 int，无需 .value）与 ERROR_PIPE_CONNECTED(535) 竞态丢连接，孤儿助手堆积刷屏消失。

- 锁屏注入助手已能在锁定会话中成功启动并连回服务（v0.8.25 日志确认 injector spawned / injector starting / app connected），本版收尾两个残留 bug：
- 修复服务拿不到注入助手子进程句柄的问题（'int' object has no attribute 'value'）：PROCESS_INFORMATION.hProcess 是结构体字段、取出即为 int，无需再取 .value；此前失败导致服务每 10 秒重复拉起、堆积大量孤儿注入助手（日志刷 rejected pipe client）。升级安装时旧孤儿助手会被自动清理。
- 修复注入助手连回服务时的竞态丢连接：客户端恰好在 CreateNamedPipe 与 ConnectNamedPipe 之间连入时，ConnectNamedPipe 会报 ERROR_PIPE_CONNECTED(535)，现在视为连接成功。

- 主窗口左下角新增联系方式：「有 bug 请联系微信：kiwi37」。
- 新增锁屏控制服务（Windows，安装时勾选「锁屏时仍可控制」）：以 LocalSystem 服务常驻后台，通过复制 winlogon 令牌在控制台会话里拉起注入助手，锁屏/登录前都能接收鼠标键盘——跨屏输密码解锁成为可能。服务开机自动启动。
- 服务在用户登录瞬间直接拉起主程序，不再等开机启动项排队，解锁后即可跨屏。
- 锁屏下的注入失败改为瞬时阻断（半秒内自动恢复），不再把共享暂停 5 秒并广播不可用。
- 安装器升级时自动停掉旧主程序与旧服务，不再弹出「DeleteFile failed; code 5 → Try again」；服务安装全程写日志到 `C:\ProgramData\MouseShare\service.log`，静默失败可诊断。
- 修复三层只有 Windows 才能暴露的服务端崩溃：WTSEnumerateProcesses 读错列（把用户 SID 当进程名）、OpenProcess 用错模块（在 win32api 不在 win32process）、DuplicateTokenEx 常量归属错误（MAXIMUM_ALLOWED 不在 win32security）。此前锁屏服务每 10 秒崩溃一次、注入助手从未成功启动。新增 pywin32 用法静态 guard 测试防再犯。
- v0.8.23：注入助手的拉起（CreateProcessAsUserW）改用 ctypes 全类型声明模式，与已验证可用的主程序拉起路径一致。
- v0.8.24：pywin32 的 DuplicateTokenEx 包装层跨版本对「安全属性」参数的校验不一致，彻底绕开 pywin32——改用 ctypes 按 MSDN 原生签名直调 advapi32 的 DuplicateTokenEx（主程序拉起与注入助手拉起共用同一 helper）。此版证明令牌复制已打通。
- v0.8.25：两个拉起环节的修复——①Python 3.13 移除了 ctypes c_void_p 句柄的 int() 转换（报 invalid literal for int()），改用 .value 显式取值；②CreateEnvironmentBlock/DestroyEnvironmentBlock 在 userenv.dll 而非 advapi32（此前报 function not found），修正 DLL 归属。

Lock-screen control arrives on Windows: an optional LocalSystem service spawns an in-session injector via the winlogon token, so the locked (or pre-logon) desktop can be driven from the other computer — including typing your password. The service auto-starts on boot and launches the GUI the moment you sign in. Installer no longer pops "Try again" on upgrades (it closes old processes itself) and logs every service-install step. Also fixed a chain of Windows-only crashes in the service (wrong WTS column, wrong OpenProcess module, misattributed security constants, the unreliable pywin32 DuplicateTokenEx wrapper (token duplication now calls advapi32 directly via ctypes), Python 3.13 removing int() support on ctypes handles (now read via .value), and CreateEnvironmentBlock being resolved from userenv.dll where it actually lives), and added a footer line with the contact WeChat: kiwi37.

---

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
