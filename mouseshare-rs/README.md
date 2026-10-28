# MouseShare（Rust 版）

局域网内共用一套键鼠 + 剪贴板文本同步。不依赖 Python、不依赖 Qt，单个可执行文件。

- macOS 编译产物：**1.3 MB**
- 与 Python 版（0.7.x）协议不兼容，两者不能互通，可并存但端口冲突，测试时改 `port`。

## 现状

已完成：

- 每台机器自签 P-256 证书，mTLS 双向认证，TLS 1.3（rustls + ring）
- 局域网广播发现（服务端口前一位）+ 手动指定地址
- 控制权交接：顶到配置的那条边连续两次即交出控制，沿原路推回来即收回，`Ctrl+Alt+Esc` 强制收回
- 键鼠事件转发（鼠标走相对位移，两台分辨率不同也能用）
- 剪贴板文本双向同步（400ms 轮询）
- 交出控制时隐藏本机光标；被控端检测到本机真实键鼠活动立即收回

还没做：

- 托盘图标与菜单（目前是命令行守护进程）
- 多机链式跳转（现在只支持直接相邻的边；被控时不会再往第三台跳）
- 多显示器的完整虚拟桌面尺寸（现在取主屏，可在配置里用 `screen` 手动指定）
- 配对确认流程（`auto_trust` 默认 true，首次连接直接信任；六位核对码函数已写好待接）
- 文件传输
- 开机自启、安装包、CI

## 编译

```bash
cargo build --release
```

## 使用

```bash
./mouseshare init 本机名字        # 生成配置与证书
# 编辑配置文件，填 edges 和 peers
./mouseshare status               # 看名字、端口、指纹
./mouseshare run                  # 常驻运行
./mouseshare run --no-input       # 不捕获也不注入键鼠，只跑网络（本地联调用）
```

配置文件位置：

| 系统 | 路径 |
| --- | --- |
| macOS | `~/Library/Application Support/MouseShare/config.json` |
| Windows | `%LOCALAPPDATA%\MouseShare/config.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/mouseshare/config.json` |

两台机器各填一半，例如左边这台：

```json
{
  "name": "aaa",
  "port": 24872,
  "edges": { "bbb": "right" },
  "peers": [{ "name": "bbb", "addr": "192.168.1.20:24872" }],
  "auto_trust": true,
  "screen": [1920, 1080]
}
```

右边那台把 `edges` 写成 `{ "aaa": "left" }`，`name` 写成 `bbb`。

`addr` 可以不填，靠广播发现。连接方向由名字字典序决定（小的主动连大的），所以两边不会各建一条。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.rs` | 命令行、监听/发起连接、每条连接的读写循环 |
| `session.rs` | 控制权状态机（本地 / 转发中 / 被控中）与越边判定 |
| `proto.rs` | 网络报文与 rdev 事件互转 |
| `transport.rs` | rustls 配置、证书指纹验证器、分帧读写 |
| `identity.rs` | 证书生成、指纹、六位核对码 |
| `discovery.rs` | UDP 广播发现 |
| `clipboard.rs` | 剪贴板轮询与写入 |
| `cursor.rs` | 各平台隐藏/显示光标 |

键鼠捕获与注入全部走 `rdev`，三个平台共用一套代码。

## 已知坑

光标贴到屏幕边缘后操作系统不再给出位移，所以交出控制时会把本机光标拉回离边 64px 处（`NEUTRAL`），保证还能继续读到移动量。这个数值和"连续两次顶边才切换"的判定都还需要真机调。
