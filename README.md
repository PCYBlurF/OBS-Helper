# OBS 权限助手 · 抖音直播伴侣推流码获取器

> 一个 Windows 图形界面小工具：在**抖音直播伴侣**里点一下「开始直播」，它就能自动把你
> 用 OBS 推流所需要的 **RTMP 推流服务器地址 + 串流密钥（推流码）** 抓出来并展示、一键复制，
> 之后在 OBS 里填入即可代替直播伴侣进行推流。

> **仓库说明**：本仓库以「Python + mitmproxy」版本为当前可用的主程序（即 `app/`、`run.bat`）。
> 早期做过一版 **React + Tauri v2** 的桌面重写（原生 Rust MITM），因兼容/抓码不稳定未采用，
> 现仅作为参考归档在 `_archive/tauri-v2/`（已被 gitignore，不入库），需要时再启用或删除即可。

---

## 1. 它解决什么问题

抖音直播伴侣官方不直接给你「推流码」，普通用户没法直接把直播间交给 OBS 推流。
但开播的时候，直播伴侣内部一定会向抖音上报/拉取**推流地址**（`rtmp://...` + 签名密钥）。
这个工具做的就是：**在你的电脑上做一层 HTTPS 中间人代理，把直播伴侣请求里的推流地址拦下来解析出来**。

## 2. 原理简述

1. 本工具启动一个本地 HTTPS 代理（基于 [mitmproxy](https://mitmproxy.org/)），监听 `127.0.0.1:8080`。
2. 把你电脑的**系统代理**指向这个代理，并通过 `certutil` 把 mitmproxy 的 CA 证书安装到
   「受信任的根证书颁发机构」，这样代理就能解开 HTTPS 内容。
3. 你在直播伴侣点「开始直播」时，直播伴侣会向抖音服务器发请求，我们拦截并解析两类数据：
   - 开播接口 `/webcast/room/create/` 响应中的 `data.stream_url.rtmp_push_url`
   - 上报日志 `log-snssdk.zijieapi.com/video/v1/live_log/` 请求中的 `push_url`
   - （以及任意抖音域名 HTTP 内容里出现的 `rtmp://` 地址，做兜底）
4. 抓到后自动拆分成 **OBS 服务器地址 + 串流密钥**，显示在界面并写入 `captured.json`。
   默认**不会**自动结束直播伴侣（保留它在推，避免房间先关闭），由你自己掌控何时切到 OBS。

图表（伪代码）：

```
直播伴侣 ──(走系统代理)──> 本地 mitmproxy ──> 抖音服务器
                              │
                              └─ 命中 rtmp:// 地址 → 解析 → 显示在界面 / 写入 captured.json
```

## 3. 目录结构

```
OBS权限助手/
├─ app/
│  ├─ capturer.py      # mitmproxy 抓流插件（捕获+解析 rtmp 推流地址）
│  ├─ proxy_utils.py   # 证书安装、系统代理设置/还原、结束进程
│  ├─ gui.py           # tkinter 图形界面主程序
│  └─ __init__.py
├─ requirements.txt    # 依赖：mitmproxy
├─ run.bat             # 一键启动（自动检查/安装依赖）
├─ build.bat           # 用 PyInstaller 打包成 dist\OBSHelper.exe
└─ README.md
```

运行后生成的数据都在 `%USERPROFILE%\.obs_helper\`：
- `captured.json`     抓到的最新推流信息（服务器地址、串流密钥、完整地址）
- `seen_flows.log`    匹配到的抖音 HTTP 流记录（排查用）
- `debug\`            开启「排错抓包」后导出的请求体（排查用）

mitmproxy 的 CA 证书默认在 `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer`。

## 4. 快速开始（脚本版）

环境要求：Windows + Python 3.10+（推荐 64 位）。

1. 双击 `run.bat`：它会先检测并安装 `mitmproxy`，然后弹出图形界面。
2. 也可以手动：
   ```bash
   pip install -r requirements.txt
   python gui.py        # 在 app 目录下，或 python app/gui.py
   ```

首次打开界面后，按界面上的步骤操作：

| 步骤 | 按钮 | 说明 |
|------|------|------|
| ① | 启动代理 | 自动生成 CA 证书并监听 `127.0.0.1:8080` |
| ② | 安装证书 | 弹 UAC，把 CA 证书装入「受信任的根证书颁发机构」（只需一次） |
| ③ | 设置系统代理 | 让本机流量走本地代理 |
| ④ | 打开直播伴侣 | 登录 → 点「开始直播」 |
| ⑤ | （抓码后） | 界面提示：去 OBS 填服务器+密钥并【开始推流】；连上后再点⑤结束直播伴侣、①停代理、④还原代理 |

然后打开 **OBS** → 设置 → 直播 → 服务选「自定义」，把界面显示的
**服务器地址** 填到「服务器」，**串流密钥** 填到「串流密钥」，点「开始推流」即可。

> 提示：抖音的推流地址带有时效（`txSecret`/`txTime`），抓到后请尽快在 OBS 里用上。
> 若勾选了界面上的「抓到后自动收尾」，则抓到码后会自动完成 结束直播伴侣 + 停代理 + 还原代理。

## 5. 打包成独立 exe（可选）

双击 `build.bat`，或手动：

```bash
pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name OBSHelper ^
  --collect-all mitmproxy --collect-all mitmproxy_rs ^
  --hidden-import h2 --hidden-import hyperframe --hidden-import hpack --hidden-import wsproto ^
  app/gui.py
```

最终文件在 `dist\OBSHelper.exe`（因为内置了 mitmproxy，体积较大，约 80~120MB，属正常）。
打包后**无需**装 Python，双击 exe 即可运行。

## 6. 常见问题 / 排错

**Q: 点「开始直播」后没有抓到推流码？**
- 确认端口没被占用（界面会检测）。
- 确认 CA 证书已安装、系统代理已设置。
- 打开界面上的「开启排错抓包」，再开播一次；然后把 `%USERPROFILE%\.obs_helper\seen_flows.log`
  和 `debug\` 里的内容发出来，我据此调整拦截规则。
- 抖音接口偶尔会变；本工具会对「所有抖音域名 HTTP 内容里的 rtmp://」做兜底扫描，
  若新地址走的是 WebSocket 则暂不支持（可后续扩展）。

**Q: 点了「开始直播」程序没反应 / 直播伴侣直接退出了？**
- 若你没勾「自动收尾」，工具默认**不会**结束直播伴侣。若直播伴侣退出了，多半是你手动关了它，
  或它本身开播异常。勾上「自动收尾」后才会在抓到码时主动结束直播伴侣。

**Q: OBS 推流失败 / 连不上？**
- 若你用了「自动收尾」，系统代理和代理已自动还原/停止；若手动操作，请确认已**还原系统代理、停止代理**。
- 服务器地址/串流密钥要完整，尤其密钥后面的 `?txSecret=...&txTime=...` 别漏。
- 若要接管同一个房间，尽量让 OBS 在直播伴侣还开着时就推流（减少“空档”），再关闭直播伴侣。

**Q: 端口 8080 被占用？**
- 换个端口（比如 8888）。换端口后「设置系统代理」会同步用新端口。

**Q: 设置了系统代理但直播伴侣似乎没走代理（一直抓不到）？**
- 个别版本的直播伴侣可能忽略系统代理。此时用 **Proxifier** 这类软件，强制
  `直播伴侣.exe` 走 `127.0.0.1:8080`（HTTP 代理）即可，本代理无需改动；
  配合已安装的 CA 证书同样能抓到。用 Proxifier 时**不要**再设系统代理，
  这样 OBS 等其它程序不受影响，体验更干净。

**Q: 安装证书报错（已存在）？**
- 提示证书已存在即可，忽略；或先删除 `certut -delstore Root "mitmproxy"` 再装。

**Q: 没有管理员权限？**
- 安装证书需要管理员，会弹 UAC 让你确认；系统代理（HKCU）无需管理员。

> 重要：**不要私下传播他人直播间的推流地址/密钥**。本工具仅用于**你自己账号**的直播间
> 交由 OBS 推流的场景。

## 7. 许可证 / 声明

- **开源协议：GNU GPL v3**（见仓库根目录的 `LICENSE` 文件）。
  本项目遵循 GPL v3 发布：你可以自由使用、修改与分发，但任何分发（含修改版）
  都必须保留版权声明，并以 GPL v3 开源。
- 本项目仅供学习与个人合法使用，请遵守抖音平台的相关条款。
- 仅在**自己的账号**上使用，请勿用于窃取、干扰他人直播。
- 因使用本工具产生的任何后果由使用者自行承担。

---

*参考的开源思路：[DouyinLiveFlowCatch](https://github.com/0Chencc/DouyinLiveFlowCatch)、[gvs/douyin](https://github.com/gvs/douyin)。*
