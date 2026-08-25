# OBS 权限助手 (OBSHelper)

基于 **React + Tauri v2** 的桌面端应用。用于抓取**抖音直播伴侣**（Live Companion）在开播时下发的 **RTMP 推流码**，并一键将其填写到 OBS 的自定义推流设置中。

> 本工具是对旧版 `Python + mitmproxy` 方案的 Rust/原生重写，化繁为简：不依赖 Python、不打包 mitmproxy，仅需一个 Windows 安装包。

## 工作原理

抖音直播伴侣在每次开播时会向抖音服务端请求一个**有时效的推流地址**（`rtmps://...` 或 `rtmp://...`）。本工具在本机启动一个 **HTTPS MITM 代理**，对抖音相关域名的流量做中间人解密，从以下两处提取推流码：

- `room/create` 响应体（主要来源，含 `rtmp_push_url`）
- `live_log` 请求体（兜底来源）

抓到的内容会拆分为 **服务器地址** 和 **串流密钥**，供 OBS「自定义」推流使用。

## 技术栈

- **前端**：React 19 + TypeScript + Vite
- **后端**：Rust（Tauri v2）
- **MITM 代理**：[hudsucker](https://crates.io/crates/hudsucker)（`RcgenAuthority` + `rustls`，动态签发叶子证书）
- **Windows 系统操作**：`winreg`（系统代理）、`windows-sys`（提权 / 网络选项刷新）、`arboard`（剪贴板）

## 功能

- [x] 一键启动/停止本地 HTTPS MITM 代理
- [x] 自动生成并安装本机 CA 根证书（自动 UAC 提权）
- [x] 设置 / 还原系统代理（含 `localhost` 等排除项）
- [x] 从 `room/create` 与 `live_log` 中自动提取推流码
- [x] 自动拆分「服务器地址」与「串流密钥」，一键复制
- [x] 实时日志、状态徽章
- [x] 退出时自动还原系统代理
- [x] 一键结束直播伴侣进程
- [x] 打包为 **nsis 安装程序 (.exe)**

## 使用步骤

1. **启动代理**：在应用内点击「① 启动代理」（默认监听 `127.0.0.1:8080`，可在「设置」中修改）。
2. **安装证书**：点击「② 安装证书」将本机 CA 加入系统受信任根证书（如非管理员运行会触发 UAC 提权）。
3. **设置系统代理**：点击「③ 设置系统代理」，本机代理即接管浏览器与直播伴侣的流量。
4. 打开 **抖音直播伴侣** 并点击「开始直播」。
5. 应用内会自动显示抓取到的**服务器地址**与**串流密钥**。
6. **还原系统代理**：点击「④ 还原系统代理」。
7. 在 **OBS** 中设置：

   - 「设置 → 推流 → 服务」选择 **自定义**
   - **服务器** 粘贴抓到的「服务器地址」
   - **串流密钥** 粘贴抓到的「串流密钥」

   点击「开始推流」即可。

> **提示**：推流码有时效，请尽快使用。RTMP 流量 **不会** 经过本机代理，因此在 OBS 推流前务必先「④ 还原系统代理」。

## 构建

> 前置：Rust 工具链、Node.js、WebView2（Win10/11 通常已内置）。具体见 [Tauri 环境要求](https://v2.tauri.app/start/prerequisites/)。

```bash
# 安装前端依赖
npm install

# 开发模式
npm run tauri dev

# 打包为 nsis 安装程序（产物在 src-tauri/target/release/bundle/nsis/）
npm run tauri build
```

## 目录结构

```
├── src/                    # React 前端
│   ├── App.tsx             # 主界面
│   └── App.css             # 样式
├── src-tauri/              # Rust 后端
│   └── src/
│       ├── lib.rs          # 入口 + 命令注册 + 退出还原逻辑
│       ├── commands.rs     # Tauri 命令与 AppState
│       ├── proxy.rs        # hudsucker MITM 代理 + 推流码提取
│       └── sys.rs          # 证书 / 系统代理 / 提权 / 进程
├── legacy-python/          # 旧版 Python 参考实现（仅存档，不参与构建）
└── ...
```

## 风险提示

- 使用 OBS 采集抖音直播伴侣的画面并推流，属于**第三方推流**，可能触发平台风控。请优先使用**小号 / 测试账号**进行验证。
- 本机 CA 证书会授予对本机流量的解密能力，仅建议在测试环境使用，结束使用后可通过「④ 还原系统代理」与卸载证书恢复。
- 本工具仅供个人学习与测试使用。
