# nanoMuse 私有化部署审计报告

- 审计对象：`knsew/nanoMuse` fork，工作副本 `~/workspace/nanoMuse/`，main @ `5020df29`（v1.0.9 之后 674 提交）
- 审计日期：2026-11-20
- 审计方式：只读源码审计，未改任何代码、未提交
- 目标：改成自己部署的 Docker 服务使用，不连接官方任何账号/服务；**从源码端彻底清理官方耦合**（不止于环境变量可关）

---

## 1. 结论先行

### 私有部署是否可行：可行，而且官方自托管支持是一等公民

nanoMuse 的自建 relay 是官方正式支持的部署形态，不是 hack：`docs/self-hosting.md`、`scripts/self-host.sh`、`cloud/docker-compose.yml`、`cloud/docker-compose.local.yml` 全套齐全。核心请求链路（`nanomuse/llm/openai_chat.py` 直接调任意 `base_url`，无白名单、无域名校验）与自建 relay 都是原生支持，**不需要改任何核心逻辑**。

更关键的两个好消息：

1. **零第三方上报/推送 SDK**：全仓无 Sentry、Firebase、FCM/APNs、广告、统计、支付类 SDK。Android 崩溃用 ACRA core 仅本地落盘，iOS 用 MetricKit 本地写盘，桌面无 crashpad 启用调用。跨设备"推送"走自建 relay 的 WebSocket 长连（`/v1/hub`）+ 本地通知，改 relay 地址即私有化。
2. **零代码改动即可私有跑起来**：`.env` 里写 `SIGNUP_OPEN=0` + `ALLOWED_IDENTIFIERS` + `CODE_SENDER=log` + `ALLOWANCE_CNY=0` + `RECORD_TURNS=0` + `GITHUB_COLLECT=0` + `CLOUD_GEOIP=0` + `INVITE_BONUS_CNY=0` + 自有 `UPSTREAM_BASE`/`UPSTREAM_KEY`/`CLOUD_MODELS`，管理页再关 nudges，即可断开官方。

### 大致工作量

| 档位 | 内容 | 工作量估计 |
|---|---|---|
| A. 零代码部署 | 只改配置 + `.env`，官方代码原样跑 | 半天（含 Docker 部署与验证） |
| B. 源码清理（relay + runtime） | 删多租户模块、遥测、更新检查、改默认值 | 约 1–2 天（Python/TS 为主，测试同步改） |
| C. 客户端清理（Android/iOS/桌面） | 改默认 relay、删更新检查、重编译打包 | 约 1–2 天（Android 默认 relay 改可空是最大单点；iOS 需 macOS 打包验证） |
| D. 目录瘦身 | `providers.json` 砍到只留本地条目 + 4 份拷贝同步 | 半天（主要是测试同步改） |

B+C+D 合计约 3–5 天。建议顺序：先 A 跑通验证 → 再 B（relay 侧）→ 再 C（客户端）→ 最后 D。注意：**源码级深度删除会增加后续 upstream 同步的合并成本**（fork 与上游 main 同步时，删掉的大块代码每次都可能冲突）。建议清理时按模块分提交（一个模块一个 commit），保留可 rebase 的粒度；或者把"可配置关闭"的项先用配置关掉，只对配置关不掉的项动源码。验证口径（Ji 2026-11-20 定）：fork 是公开仓，**可直接在 GitHub 上构建验证**（推 main 即触发 `docker.yml` + `ci.yml`，公开仓免费）；打 tag / 建 Release 仍等他通知。

### 最重要的三个必改项（配置关不掉的）

1. **遥测无开关，登录即开**：`nanomuse/telemetry.py` 默认上传客户端事件 + 自有 key 的完整 turn 到 relay，无任何环境变量/配置项可关。只能源码删（`telemetry.py` 整文件 + 约 10 个调用点；harness 桌面端还有同款 `OwnTurns`，必须一起删）。
2. **nudges 未登录回退官方**：桌面插件 `harness/dsh-nanomuse/src/nudges.ts:86` 的 `NUDGES_ORIGIN = 'https://cloud.nanomuse.cn'`，未登录的 own-key 安装每天仍请求官方 relay 一次拉"求 star"文案。必改。
3. **更新检查默认开且三端无开关**：Python 可配关（`NANOMUSE_NO_UPDATE_CHECK`），harness/Android/Windows 桌面均无关闭开关，全部默认向 `nanomuse.cn/dl/index.json` + `api.github.com` 查新版本。源码清理需删 4 个独立实现。

---

## 2. 可直接去掉的清单（源码删除）

> "断不断"指后端能否启动、核心链路（登录/chat/hub/sync）是否正常。通用原则：**删代码、不删 DB 列**（SQLite 删列要重建表，老库迁移风险大；留着无害）。

### 2.1 relay（`cloud/`）可删模块

| # | 模块 | 清理方案 | 断不断 | 牵连测试 |
|---|---|---|---|---|
| R1 | 应用商店评审码 | 删 `service.py:722-744` `_review_identifiers`、`:750-752` `is_review`、`:462-464` 初始化；`allowed()`（:757）等 4 处去掉 `is_review` 豁免；`request_code`（:825-861）删 review 分支、code 恒随机；删 `config.py:594-595` 字段 + `.env.example` 评审段 | 不断 | `tests/test_review.py` 整文件删；`test_hub.py` 内 review 用例 |
| R2 | GitHub stars 采集器 | 删 `nanomuse_cloud/github_stats.py` 整文件（280 行）；删 `api.py:160,479,495,608-609,621` 接线与 `GET/POST /v1/admin/github`（:2561-2571）；`stats.py:2558` 改传 `None`（`Stats` 已做 None 兼容）；删 `stats.py:439-489`、`config.py:677-680,707` 字段；前端删 `console/src/pages/Site.tsx` 的 github 段 | 不断（纯后台任务 + 只读页） | `tests/test_github.py` 整文件删；`test_console.py` 内 github 用例 |
| R3 | demo/showcase 网关 | 删 `api.py:2099-2119` `web_info`/`demo_admin`、`:2096` 调用、`:2126-2134` 两个 admin 路由；删 `service.py:3404-3480` `admin_turns` 所在的 `admin_traffic()`；删 `config.py:650-658` 四个字段；前端删 `console/src/pages/Demo.tsx` + `console/src/lib/demo.ts` | 不断（只读 admin 面板） | `test_console.py` 内 demo/traffic/web 用例；`demo.test.ts`、`Demo.test.tsx` 整文件删 |
| R4 | 短信（Aliyun） | 删 `senders.py:172-287`（`AliyunSmsSender` 整类及签名 helper）、`:321-334` `BothSender`；`make_sender`（:302-320）只留 log/smtp 分支；删 `config.py:617-622` 五个字段 + `.env.example` 阿里云段。保留 SMTP + log | 不断（sender 是注入式 Protocol）。后果：手机号注册只能用 `CODE_SENDER=log` 读日志取码 | `tests/test_senders.py` 删 aliyun 部分 |
| R5 | star 索要 nudges（relay 侧） | 删 `nanomuse_cloud/nudges.py` 整文件（176 行）；删 `service.py:39-42` import、`:54`、`me()` 里的 `"nudges"`（:1657）、`:1662-1705` 三个方法；删 `api.py:818-823`、`api.py:2636-2647` 路由；前端删 `Settings.tsx` nudges 段 | 后端不断。**注意**：客户端内置 `DEFAULT_NUDGES`，relay 删接口后客户端会用内置默认继续弹 star，必须同时改客户端（见 §4.5）。推荐过渡：保留 `GET /v1/nudges` 一个路由硬编码返回 `{"star": {"enabled": false}}`，确认客户端行为后再删路由 | `tests/test_nudges.py` 整文件删；`test_console.py` 内 nudges 用例；`Settings.test.tsx` 内 nudges 用例 |
| R6 | 邀请奖励 | 删 `service.py:1004-1100`（`normalize_invite`、`_accept_invite`、`settle_invite`、`invite_code_for`、`invite_view`）、`:3603-3720`（admin 邀请函数）；删 `db.py:2061-2263` 区间 13 个 invite 函数；摘掉 `service.py:958,1481,1527,1633,1869,1877,1901` 调用点、`api.py:829-832,2220-2244,2073` 路由、`config.py:536,554` 字段；前端删 `console/src/pages/Invites.tsx` 整页及各处引用 | 后端不断。**客户端风险未确认**：`/v1/me` 少 `invite` 字段，客户端是否容忍缺字段需看客户端代码。稳妥过渡：先保留 `me()` 的 `invite` 字段返回空结构，确认后再删 | `test_cloud.py`、`test_accounts.py`、`test_analytics.py`、`test_settings.py`、`test_console.py`、`test_operator_actions.py`、`test_review.py`、`test_hub.py` 内 invite 用例；`test_allowance.py` 部分 |
| R7 | 计费门禁（轻量档，推荐） | 只删门禁与文案，**保留 ledger 记账**：删 `service.py:1507-1556`（`_exhausted`、`_paused_allowance`）、`:1468-1505`（`ways_on`、`guidance`）；`_check_budget`（:1816-1830）内 `if self.limited(caller):` 分支删（或把 `limited()` :1429-1430 改为恒返 `False`）。`api.py` 五处 `check_budget` 调用**保留**（仍需 per-minute 限流 + in-flight 预留） | 不断。全删档（连 ledger 一起删）不推荐：会断 admin 统计页，牵连面大 | `tests/test_allowance.py`（门禁类用例删，记账类保留）；`test_cloud.py`、`test_analytics.py`、`test_stats.py`、`test_console.py`、`test_settings.py`、`test_controls.py` 内 allowance 用例 |
| R8 | GeoIP | 删 `nanomuse_cloud/geo.py` 整文件（333 行）；删 `api.py:159,490-491,607,634,2087,2144-2156` 接线，`with_places` 改为空 dict，`client_place()`（:660-663）恒返 `None`；`service.py:1461-1466` `region()` 只留大陆手机号分支、其余返 `"unknown"`；删 `config.py:664-671` 字段；前端删 `console/src/pages/Places.tsx` 整页 | 不断（全是防御式调用）。功能影响：管理页失去地域分布 | 无独立 `test_geo.py`；`grep -rn "geo\|place\|Places" tests/` 命中的用例按个调整（主要在 `test_cloud.py`、`test_analytics.py`、`test_console.py`、`test_accounts.py`） |
| R9 | RECORD_TURNS（a 档：只关自动记录，推荐过渡） | `api.py:1306-1330` `record_later()` 改为空操作（或删函数 + 删两处调用）；`service.py:2272` 起的 `record_turn()` 可保留（不再被调用）。效果等同 `RECORD_TURNS=0`，但 `/v1/turns` / `/v1/blobs` 的用户上传与查询接口仍可用 | 不断 | 少量 record 相关用例 |
| R10 | RECORD_TURNS（b 档：全删，最后做） | 删 `nanomuse_cloud/record.py` 整文件（365 行），但先处理两个外部引用：`analytics.py:38` 的 `unpack`（把 `pack`/`unpack` 移到 `db.py` 或新建小模块）、`turnview.py:20` 的 `BLOB_PREFIX`（随 record 一起删 `turnview.py` 即可）；删 `service.py:2272` 起 `record_turn` 及 `:2300-2900` 区间 16 个 record 函数；删 `api.py:898-1010` 的 `/v1/turns`、`/v1/blobs` 路由与 `:2278-2340` admin 路由；删 `service.py:1395,2251,3679,3874` 四处 `purge_record` 调用；前端删 `Turns.tsx` + `turns.ts`。**删账号残留风险**：`purge_record` 删掉后删账号会在 turns 表留孤儿行，需在 `db.delete_account` 内补删该账号 turns 行（`db.delete_account` 现状是否已清 turns 未确认，需核对） | 后端可不断，但功能影响大：全设备共享对话记录（含截图）是 1.0.9 主推功能之一。**客户端行为未确认**：路由整个删掉后客户端对 404 的处理需看客户端代码。建议先做 a 档 + `RECORD_TURNS=0` 跑一段时间确认后再做 b 档 | `tests/test_record.py`、`tests/test_turns.py` 整文件删；`test_analytics.py`（`unpack` 改动处）、`test_provenance.py` 内 record 用例；`console/src/lib/turns.test.ts` 删 |
| R11 | DashScope 媒体代理（可选） | `CLOUD_MEDIA_ENABLED` 默认已是 `0`，媒体路由已是 410，不删也不影响功能。彻底清干净：删 `api.py:1652-1943` 的 `_dashscope_post_image` / `_dashscope_image` / `_dashscope_reply` 及图片/视频路由，删 `config.py:447-448` 的 `DASHSCOPE_*` 字段，删 `api.py:449` 的 `PROVIDER_HOST_SUFFIXES`（`.aliyuncs.com` / `.alicdn.com` 白名单） | 不断 | `test_cloud.py` 内媒体路由用例（应为 410 断言） |
| R12 | 官方生产部署脚本 | 删 `cloud/deploy/nanomuse-hk/` 整目录（11 个文件：官方 `cloud.nanomuse.cn` 香港生产 relay 的 Caddy/systemd/backup 脚本）。无代码引用该目录 | 不断任何构建 | 无 |
| R13 | 训练样本（contribute，可选） | `IMPROVE_DEFAULT` 默认已是 `0`，留着无数据。如要连训练样本一起删：删 `service.py:3039` `keep_sample` + `:2224` `set_contribute` + `api.py:847` `POST /v1/me/contribute` + db 的 samples 相关函数 | 不断 | 相关 contribute 用例 |

**推荐清理顺序**：R1 评审码 → R2 GitHub 采集器 → R3 demo 网关 → R4 短信 → R5 nudges → R6 邀请 → R7 计费轻量档 → R8 GeoIP → R9 RECORD a 档；R10（RECORD 全删）与计费全删最后做，且先确认客户端行为。

### 2.2 遥测（无开关，必须源码删）

| # | 位置 | 清理方案 | 断不断 | 牵连测试 |
|---|---|---|---|---|
| T1 | Python：`nanomuse/telemetry.py` 整文件 | 删除。删接线：`hub/service.py:41,106,175,236,532`（import、`install`、`start`、`stop`、`forget`）；删调用：`server/service.py:67,1092-1097,1128,1139-1144`（`turn.ended` / `turn.feedback` / `turn.stopped` 三处事件）；删 `sentinel/gate.py:41-50` 的 `_note()` 及全部调用点（数量未展开，动手时 grep `_note(`）；删 `phone/operator.py:820-870` 附近的 `_note_step()` / `_note_outcome()` 及调用点；删 `llm/base.py:257-290` 的 own-key 上传整块（保留 :250-256 的 `to_cloud` 分支，Cloud 直连 turn 归属不受影响）；删 `llm/base.py:16,20-26,328` 的 `_upload_turn` / `without_turn_upload()` 机制；`sentinel/review.py:16,213` 去掉 `with without_turn_upload():` 包裹、块内代码降一级缩进；删 `cloud.py:220-257` 的 `post_turn` / `post_events` / `has_blob` / `put_blob`（已确认仅 telemetry 调用）；顺手改 `llm/session.py:22` 提及 telemetry 的注释 | Python 无编译期检查，删 import + 调用点即不报错。功能上只丢失"账号记录 / 训练视图"。可选一并删除"Help improve"开关链：`cloud.py:211-213` `set_contribute`、`hub/service.py:476-480`、`server/api.py:1409-1416`、web `api.ts:190` `cloudContribute` 及 DataControls 开关 UI（遥测删完后该开关失去意义；若保留 relay record 功能则保留） | `tests/test_provenance.py` 整文件基本围绕 telemetry——整文件删或重写；`tests/test_sentinel_review.py:22,397-407`（`without_turn_upload` 相关用例需删/改）。grep `telemetry` 在 `tests/` 仅命中这两个文件，动手时再 grep 确认 |
| T2 | harness 桌面端同款遥测（必须一起删，否则桌面端仍在上传） | 删 `harness/dsh-nanomuse/src/cloud.ts:772-792` 的 `new OwnTurns({...})` 整块、`:734` 的 `ownTurns` 字段、`:63` 的 `record.ts` import；`:1226` 改为直接 `next()`；删 `:1370-1371` `recordOwnTurns()`、`:1363` `recordOff` 及 `:1447,:3481` 的复位；删 `:808` 的 `relay.postEvents(...)` 调用及其外围事件队列函数（外围函数名未展开，动手时以 :808 为起点确认）；删 `relay.ts:660-700+` 的 `hasBlob`/`putBlob`/`postTurn`/`postEvents`（**未确认**是否仅 OwnTurns/事件队列调用，动手时 grep 全仓库确认后再删）；`src/record.ts` 若除 `cloud.ts` 外无其他引用则整文件删除（动手时 grep `from './record` 确认） | 不断（OwnTurns 只负责上传自有 key 的 turn） | `harness/dsh-nanomuse/tests/cloud-provenance.test.mjs` 很可能覆盖 OwnTurns（文件名推断，未展开，动手时核对）；另 grep `tests/` 中的 `ownTurns\|OwnTurns\|postTurn` |

背景证据：遥测发到**当前配置的 relay**（不是硬编码官方地址），走 `POST /v1/events`（`cloud.py:228-233`）、`POST /v1/turns`（`cloud.py:220-226`）、`HEAD/PUT /v1/blobs/{sha256}`（`cloud.py:235-257`）。**默认开、无关闭路径**：`ready` = 已安装 cloud 客户端且 vault 里有 key（`telemetry.py:81-82`）；`hub/service.py:106,175` 无条件 `install` + `start`；`upload_turns = True` 是硬编码默认值（`telemetry.py:62`），全仓库 grep 无任何地方把它设为 `False`；全仓库 grep `NANOMUSE_TELEMETRY` / `telemetry.*off|disable` 无结果。唯一可靠办法是退出登录。注意"Help improve"开关 ≠ 关闭遥测：该开关只是 `POST /v1/me/contribute`，由 relay 端决定是否保留训练视图；本地照常上传事件与完整 turn（Android `NanoMuseCloud.kt:102-103` 注释："the record (relay 0.29) — every turn in full, whatever the switch says"）。

### 2.3 更新检查（4 个独立实现，全部删掉）

| # | 位置 | 清理方案 | 断不断 | 牵连测试 |
|---|---|---|---|---|
| U1 | Python + web：`nanomuse/server/update.py` 整文件 | 删 `server/api.py:95` 的 import 与 `:588-594` 的 `/api/update` 路由整段；删 `config.py:703-706` 的 `update_check` 字段；删 `web/src/api.ts:344` 的 `update` API、`web/src/types.ts:868-880` 的 `UpdateView` 接口；简化 `web/src/screens/SettingsScreen.tsx:89,102-115,573,611-645,651,657,828`（删 `release` state、`checkRelease`、`VersionRows` 的 latest/checkNow/Update 链接/checked_at 四块及 About 行徽标，只留版本号一行） | 设置页不再显示"有新版本"，其余无影响；无后台任务依赖它 | `tests/test_failures.py:242-320` 三个 update 测试删除；`tests/test_nudges_runtime.py:21,204-234`（只删 update 相关用例与 `update` import，保留 nudges 部分）；`tests/test_server.py:3114-3118` `test_update_endpoint` 删除 |
| U2 | harness dsh-nanomuse：`desk.ts` + `cloud.ts` | 删 `src/desk.ts:517-519,616-672,673-675,683-695`（`RELEASES_API`/`RELEASES_INDEX`/`RELEASES_PAGE`、`checkForUpdate()`、缓存常量、`updateCacheFresh()`）；删 `src/cloud.ts:30` 的 import、`:439` 的 state `update` 字段、`:502-506` 的 `updateForThisVersion()`（`compareVersions` 若无他处调用可一并删）、`:1268-1272` 启动 15 秒定时检查、`:1276-1315` 的 `update()` + `scheduleUpdateRetry()`、`:2733` 的 status 行、`:4017` 的恢复分支、`:4566` 的 `GET /update` 路由；删 `src/client/live.ts:9,146-147,225,253` 的 `UpdateInfo` 相关 | 不断。**未确认**：更新信息的 UI 渲染位置——`client/panels.ts` 与 `client/index.ts` 内无 `UpdateInfo`/`snapshot.update` 引用，badge/About 行可能在 DSH host 侧或尚未接入；动手时 grep 全仓库 `snapshot.update` 确认 | 删除 `tests/update-cache.test.mjs` 整文件；`tests/desk.test.mjs` 删除 checkForUpdate 相关用例（:186-240 附近）及 :7 的 import；另 grep `cloud-state-write` 等测试有无引用 `state.update` |
| U3 | Windows 桌面：`harness/desktop/src/windows-updates.ts` 整文件 | 删 `main.ts:26,498,951-959,2003-2026,2048` 的 import、变量、4 个 IPC handler、创建 + `start()`、dispose；删 `preload.ts:15,24-33` 的 `updates` bridge；删 `electron-builder.yml:82-87` 的 `publish:` 块；`windows-network-cost.ts` 若仅被 `windows-updates.ts` 引用则一并删除（动手时 grep 确认）；`package.json` 的 `electron-updater` 若无其他引用则移除依赖 | Windows 不再自动检查/下载/安装更新，改走手动下载安装包；其余功能不受影响。macOS/Linux 本来就没有自动更新 | 删除 `tests/windows-updates.test.mjs`、`windows-update-feed.test.mjs`、`windows-download-policy.test.mjs`、`windows-install-handoff.test.mjs`、`windows-exit-paths.test.mjs`、`helpers/update-feed.mjs`；`tests/installer.test.mjs:44` 删除 publish URL 断言；`tests/protected-exit.test.mjs` 可能涉及 install handoff，需核对 |
| U4 | Android（两套） | 删 `community/UpdateCheck.kt` 整文件；简化 `ui/settings/VersionRow.kt`（删除 `statusLine()`、`onTap()`、`rememberVersionState()` 及 `UpdateCheck` import，只显示 installed 版本；调用方 `SettingsScreen.kt:337` 不变）；删 `com/openminis/app/data/UpdateChecker.kt` 整文件；删 `debug/DebugRPCHandler.kt:928-957` 的 check/download 两个 RPC 方法 | 设置页无"最新版本"行、无应用内 APK 下载，改走手动下载。**未确认**：`UpdateChecker` 在 About 页的调用点（main 源码内仅见 Debug RPC 调用；`UpdateCheck.kt` 注释称 About 页用它下载 APK），动手时再查 | 删除 `test/.../community/UpdateCheckTest.kt` 整文件；动手时再确认无其他引用 `UpdateChecker` 的测试 |

背景证据（四处独立实现，全部默认开启；策略统一是 `nanomuse.cn/dl/index.json` 优先、`api.github.com` 兜底）：
- Python：`update.py:29-32` 常量；触发是懒式的，仅 `GET /api/update` 时才查（`server/api.py:588-594`），web 设置页打开时调用；无后台定时。**仅 Python 可关**：`update.py:55-63` 的 `enabled()` 认 `server.update_check=false`（`config.py:706`，默认 `True`）或 `NANOMUSE_NO_UPDATE_CHECK=1`。
- harness：`desk.ts:517-519` 常量、`checkForUpdate()`（:616-672）；触发是**启动后自动**：`cloud.ts:1270-1272` 启动 15 秒后检查，失败 1 小时重试；**无关闭开关**。
- Android：新版 `community/UpdateCheck.kt:34-36` 常量，打开设置页时 `VersionRow.kt:59` 触发；上游遗留 `UpdateChecker.kt:37,43`（`OWNER = "nano-muse"`），含 APK 下载。**无关闭开关**。
- Windows：`windows-updates.ts:38-41` 的 `SOURCES`（mirror `https://nanomuse.cn/dl/win/` + GitHub Releases），启动即查 + 每 6 小时；`autoDownload` 默认开（:90），但**检查本身无关闭开关**。

### 2.4 其他可直接删的目录与文件

| # | 目标 | 说明 |
|---|---|---|
| D1 | `site/` 整目录 | legacy 落地页（`site/README.md`：主页已迁至 nano-muse.github.io，`site/index.html` 只是重定向，`legacy.html` 是旧页面）。Docker 服务部署用不上。 |
| D2 | `website/` 整目录 | 只是 VitePress 文档站（`website/package.json`：`nanomuse-docs`，srcDir `../docs`）。部署用不上；文档在 `docs/` 里，本地读 Markdown 即可。 |
| D3 | `demo/` 整目录 | `demo/showcase/` 是公开演示网关（`demo/showcase/gateway/` 含 `showcase_gateway` 服务 + Caddy），`demo/mobilegym/` 是演示用的手机操作沙箱。私有部署用不上。 |
| D4 | `cloud/deploy/nanomuse-hk/` | 见 R12。 |
| D5 | `cloud/console/` 中与被删模块对应的页面 | 随 R2/R3/R5/R6/R8/R10 逐项删（Invites、Demo、Places、Turns 等页 + router/types/locales 引用）。改完跑 `cd cloud/console && npm ci && npm run check && npm run build`（构建产物 `nanomuse_cloud/console/admin/` 是提交物，改完要重编提交）。 |
| D6 | Sentry MCP 目录条目（可选） | 不是 Sentry 上报 SDK，只是 MCP 连接器目录条目（`harness/dsh-nanomuse/src/connectors-catalogue.ts:86`，`https://mcp.sentry.dev/mcp`）。想清干净：改源头删 `sentry` 条目，再跑 `node scripts/connectors-json.mjs` 重新生成两份 `connectors.json`（CI 有 `--check`）。不断编译，不断功能。 |
| D7 | Android ACRA（可选，不推荐删） | `build.gradle.kts:320` 的 `acra-core:5.12.0` 仅本地落盘、零网络（`CrashFileReporter.kt:24-31` 注释明确"No network. No HTTP sender"）。可保留；如连本地落盘都不想要，删依赖 + `MinisApp.kt:220` 的 `ACRA.init` + `CrashFileReporter.kt` 即可。 |

---

## 3. 可配置关闭的清单（不改代码，改 `.env` / 配置即可）

> 这些项**建议先用配置关掉、跑通验证**，源码删除（§2）是第二步。全部开关均在 `cloud/.env.example` 有文档。

| # | 开关 | 默认值 | 私有部署建议值 | 证据 |
|---|---|---|---|---|
| S1 | `SIGNUP_OPEN` | `1` | `0` | `cloud/nanomuse_cloud/config.py:587`；`.env.example:58` |
| S2 | `ALLOWED_IDENTIFIERS` | 空 | 自己的邮箱/手机号（逗号分隔） | `config.py:586`；与 S1 配合即私有 relay，白名单身份 = member，自动无限额（`service.py:1312,1429-1430`） |
| S3 | `RECORD_TURNS` | `1`（**默认开**） | `0` | `config.py:468`；`docs/self-hosting.md:133` 亦确认默认开。注意：关掉不影响 `sync.py` 的会话同步（另一套跨设备聊天副本）；训练数据是另一套 `contribute` 开关，`IMPROVE_DEFAULT` 默认已是 `0`（`config.py:549`） |
| S4 | `ALLOWANCE_CNY` | `10` | `0`（=不限额） | `config.py:535`，注释 `0 = no limit`；门禁 `service.py:1429-1430` |
| S5 | `INVITE_BONUS_CNY` | `5` | `0` | `config.py:536`。无"一键关闭邀请功能"的开关，设 0 后结算仍跑但金额为 0 |
| S6 | `GITHUB_COLLECT` | `1` | `0` | `config.py:679`；启动门 `api.py:608-609`；采集间隔 6 小时（`github_stats.py:31`） |
| S7 | `CLOUD_GEOIP` | `1` | `0` | `config.py:664`；启动时后台下载 ip2region 数据库（`geo.py:34`，`raw.githubusercontent.com/lionsoul2014/ip2region/...`；`geo.py:220-248`；`api.py:607`） |
| S8 | `CODE_SENDER` | `log` | `log`（默认即好，验证码打 relay 日志，单人读日志取码）或 `smtp`（自建邮箱） | `config.py:611`（`log \| smtp \| aliyun \| both`）；分发 `senders.py:302-320` |
| S9 | `REVIEW_ADDRESSES` / `REVIEW_CODE` | 均空 | 留空（默认已关） | `config.py:594-595`；任一为空即关闭（`service.py:722-729`） |
| S10 | `TRAFFIC_DB` / `WEB_INFO_URL` / `WEB_ADMIN_URL` / `WEB_ADMIN_TOKEN` | 均空 | 留空（默认已关，demo/showcase 网关面板显示"未配置"） | `config.py:650-658`；`api.py:2099-2119` |
| S11 | `CLOUD_MEDIA_ENABLED` | `0` | 留 `0`（默认已关，媒体路由直接返回 410） | `config.py:628` |
| S12 | nudges（star 索要） | `enabled: true` | 管理页 `PUT /v1/admin/nudges` 设 `star.enabled=false`（**无环境变量**） | `nudges.py:32-36` 默认策略；`api.py:2642-2647`。注意：客户端内置 `DEFAULT_NUDGES`，relay 不可达时客户端仍会按内置策略弹 star 提示，彻底停需改客户端（§4.5） |
| S13 | `CLOUD_CATALOG` / `CLOUD_CATALOG_PROBE` | `1` | 本地端点建议 `CLOUD_CATALOG=0`，或至少 `CLOUD_CATALOG_PROBE=0`（否则 relay 会调上游 `GET /models` 并逐模型发探测请求，`catalog.py:151`；`config.py:640-646`） | `config.py:640-646` |
| S14 | `PER_MINUTE_REQUESTS` | `30` | 保留 `30`（防跑飞限流，与计费无关） | `config.py:499` |
| S15 | `SIGNUP_TOKENS` / `DAILY_CAP_TOKENS` | 均 `0` | 留 `0`（默认已不限） | `config.py:497` |
| S16 | `NANOMUSE_NO_UPDATE_CHECK` / `server.update_check` | 开 | `NANOMUSE_NO_UPDATE_CHECK=1` 或 `server.update_check=false`（仅 Python runtime 可关；其余三端无开关，见 §2.3） | `update.py:55-63`；`config.py:706` |
| S17 | `CHAT_DEFAULTS` | `{"enable_thinking": false}` | 本地端点建议先用 `{}` 起步实测（顶层 `enable_thinking` 是 Model Studio 方言；vLLM 的 Qwen 模板要 `chat_template_kwargs.enable_thinking` 形式；Ollama/halogen 是否容忍未知顶层字段**未确认**） | `config.py:491`；`api.py:332-347` `apply_chat_defaults`；`.env.example` 注释 |
| S18 | `UPSTREAM_BASE` / `UPSTREAM_KEY` / `CLOUD_MODELS` | DashScope 默认 | 指自建端点（见 §4.3） | `config.py:443-444`；`cloud/.env.example:22-27,185` |
| S19 | `INVITE_URL` / `OWN_KEY_DOCS` / `PRIVACY_URL` / `REPO_URL` / `OPENROUTER_URL` | 官方地址 | 改为自建地址或留空（均可环境变量覆盖，不用改代码） | `config.py:554,555,562,565,559` |
| S20 | `CLOUD_SECRET` / `CLOUD_ADMIN_TOKEN` | 空 | **必须设**随机串（`openssl rand -hex 32`）。`CLOUD_SECRET` 是标识符 HMAC 与 AES-GCM 密钥派生源，轮换会废掉全部账号与在途验证码；无 `CLOUD_SECRET` 且 `CODE_SENDER != log` 时启动直接拒绝（`__main__.py:32-33`） | `config.py:808-813`；`.env.example` 注释 |
| S21 | `CLOUD_DOMAIN` / `PUBLIC_BASE` | — | 自建域名（公网 + Caddy）或局域网地址（`--local` 模式） | `cloud/docker-compose.yml:18` 注释示例即官方域名，改自建即可 |

### 账号体系最小改造结论

**`SIGNUP_OPEN=0` + `ALLOWED_IDENTIFIERS` 已够用，不建议改成单用户固定 key。**

- 门禁集中在 `allowed()`（`service.py:755-757`）：`return self.s.signup_open or self.listed(ident) or self.is_review(ident)`。三条登录路径都查它，无旁路：发码 `service.py:841`、验码 `:894`、密码登录 `:916`，均抛 `403 not_invited`。启动日志明示私有模式（`service.py:475`）。
- 白名单身份 = member（`service.py:1312`），自动无限额、可点名任意上游模型（`service.py:1722-1724`），无需再动计费。
- 代码里没有"单用户固定 key"的环境变量或分支；认证是每设备签发 `nm_…` key，支持多设备各发 key、单独吊销（`/v1/me/sessions`）和 hub 设备绑定。改固定 key 需改签发、鉴权（`api.py:705-709` `caller_dep`）、hub 绑定三处，且丢失多设备独立 key、可单独吊销的能力。
- 注意事项：① 运行时覆盖风险——`signup_open` / `allowance_cny` / `invite_bonus_cny` 可由管理页在运行时修改，存入 DB 后优先于环境变量（`service.py:409-413` `RUNTIME_SETTINGS`；`config.py:531-534`），保管好 `CLOUD_ADMIN_TOKEN`；② `SIGNUP_OPEN=0` 会锁掉不在白名单的老账号（`allowed()` 每次登录都查，不只注册时查），切私有前确认白名单包含自己的全部标识；③ 另有运营总开关 `signups`（`controls.py:44`），可暂停新注册但老账号继续用，是第二道门。

---

## 4. 必须替换/改造的清单

### 4.1 默认 relay 地址（四端默认值都是官方地址，但全部可以只改配置；源码清理则改为必填配置）

| 端 | 默认值定义 | 配置覆盖方式 | 源码清理方案 |
|---|---|---|---|
| Python runtime | `nanomuse/config.py:645`，`base_url: str = "https://cloud.nanomuse.cn"` | `NANOMUSE_CLOUD_BASE_URL`（:912-913）或 `[cloud] base_url`（:1117-1118）。`required` 默认 `True`（:649），无 relay 部署可设 `false` | 改默认值为空 `""`，同步改 docstring（:641-643）注明必填；在 `load_settings()`（:1229-1250）`model_validate` 之后加 fail-fast：`required` 且 base_url 为空时抛错。空 base_url 时 `CloudClient("")` 的请求在 `cloud.py:116` 拼出 `"" + path`，httpx 抛 `InvalidURL`，被 `_request`（:120-121）转成 `CloudError(0, "offline")`，不会崩溃只会登录失败。`llm/session.py:219-230` 的 `set_cloud_base`/`is_cloud` 对空字符串安全；`nudges.py:225` 已有空保护。牵连测试：`tests/test_hub.py:112-114`、`tests/test_avatar_studio.py:388`（官方域名只作示例值，可保留或换 `example.org`）；未见断言"默认值 == 官方域名"的测试，动手后跑 `pytest tests/test_hub.py` 验证 |
| Android | `NanoMuseCloud.kt:49`，`DEFAULT_BASE = "https://cloud.nanomuse.cn"` | 登录屏"Use a different server"表单（`ui/cloud/CloudSignInScreen.kt:456-555`，含 `/healthz` 校验）；`baseUrl()` 读 SharedPreferences 的 `cloud.base`（:281-283），无值回退 `DEFAULT_BASE` | **改动量最大**：删 `DEFAULT_BASE`（:49）；`baseUrl()` 返回类型 `String` → `String?`，删回退；`setBaseUrl()`（:285-289）删 `== DEFAULT_BASE` 分支；删 `RelayAddress.kt:81` 的 `isDefault()`；登录屏 `CloudSignInScreen.kt:107` 的 `serverOpen` 初始值改 `true`，`:456-457` 可空处理，`:541-550` 删除"恢复默认服务器"按钮，`current == null` 时禁用登录并提示；账号页 `CloudAccountScreen.kt:201-206` 删 `isDefault` 判断；`ImageGen.kt:81` 删除 `!= DEFAULT_BASE` 条件；`MediaModels.kt:20-26` 删除 `isPublicCloud()` 及 `available()` 内官方判断（入参改可空，空返 `emptyList()`）。`baseUrl()` 改可空后必须逐个处理的调用点（否则 Kotlin 断编译）：`NanoMuseCloud.kt:482,492,502,519,533,730,1260`（建议在 `request()` 入口统一 `baseUrl(context) ?: throw CloudException(...)`）、`Nudges.kt:202`、`hub/Hub.kt:70,96`、`net/OwnProviderProxy.kt:156,168`、`sync/ConversationSync.kt:491`、`CloudAccountScreen.kt:263`。牵连测试：`RelayAddressTest.kt:87-89`（`isDefault` 三个断言必须删除/改写）；`NanoMuseCloudCancelTest.kt` 需核对是否依赖 `DEFAULT_BASE`；`canOverrideBase()`（:292）恒 `true`，未见除定义外的调用，动手时 grep 确认后可删除 |
| harness dsh-nanomuse | `src/cloud.ts:138`，zod schema `baseURL: z.string().default('https://cloud.nanomuse.cn')` | `NANOMUSE_CLOUD_URL` 环境变量（`cordis.patch.yml:83`）。**注意变量名与 Python 不同**：harness 是 `NANOMUSE_CLOUD_URL`，Python 是 `NANOMUSE_CLOUD_BASE_URL` | 改 zod schema 为必填：`baseURL: z.string().min(1, '...')`（删 default）；`cordis.patch.yml:83` 改为 `process.env.NANOMUSE_CLOUD_URL \|\| ''`；`cloud.ts:745` 附近构造器首行加 fail-fast（`new Relay('')` 会让后续 `fetch('/v1/...')` 在 Node 里因相对 URL 抛 `TypeError`，不如启动时明说）；删 `cloud.ts:108` 的 `OFFICIAL_CLOUD_ORIGIN` + `:111-116` 的 `relayMediaEnabled()`，6 处调用点（:1935,2981,2989,3117,3219,4699）改为 `true` 再化简（自建 relay 一律启用媒体）。牵连测试：`tests/cloud-models.test.mjs:136-158`（`relayMediaEnabled` 与官方 origin 的断言必须重写）；`tests/relay.test.mjs`（官方域名只作 mock 数据，可保留） |
| harness/desktop | `src/proxy.ts:13`，`DEFAULT_RELAY_HOST = "cloud.nanomuse.cn"` | 自建 relay 的 host 由 `trustedRelayHosts()`（:69，读 `NANOMUSE_CLOUD_URL`）追加 | **只用于 NO_PROXY 列表**（:59-66），不产生流量；可删（`noProxyList()` 改为只含 LOOPBACK + 传入的 relayHosts），保留也无害。牵连测试：`harness/desktop/tests/proxy.test.mjs`（NO_PROXY 期望值含官方域名） |
| web | 无默认值 | 所有请求走相对路径 `/api/*` 到本地 runtime（`web/src/api.ts:170+`）；`cloudBaseUrl` 从后端数据取 | 无默认值可改，只需处理外链（见 §4.5） |
| iOS | `NanoMuseCloud.swift:118,154,158,260`，`defaultBase`；`usesOwnRelay` 判断 | 同 Android 的"Use a different server"入口 | 改成自建地址。牵连 `NanoMuseLogicTests.swift`；`NanoMuseModelSlots.swift:303` 用它识别官方 |

附带行为差异（自建 relay 反而功能更多）：官方域名被用作"是否官方云"的判断——`cloud.ts:108-116` 的 `relayMediaEnabled()`（官方云禁用媒体生成，自建 relay 启用）；Android 同款 `media/MediaModels.kt:20` 的 `isPublicCloud()`。指向自建 relay 后这两处自动翻转，无需改代码。

### 4.2 默认模型上游（runtime 侧：默认是 DeepSeek，不是百炼）

| # | 动作 | 文件:行 | 改成什么 | 不改后果 / 断裂风险 |
|---|---|---|---|---|
| M1 | 改代码默认值 | `nanomuse/config.py:85-88` | `model` 改成 halogen served id；`base_url` 改成 `http://10.1.1.52:8000/v1`（或同机 `http://127.0.0.1:8000/v1`）；`provider = "openai"` 不动（它是协议名）；`api_key` 默认保持 `""`，在示例配置里填占位 | 不改则全新安装默认连 DeepSeek。不断编译；已有用户配置（显式写了 deepseek）不受影响 |
| M2 | 改示例配置默认块 | `config/config.example.toml` `[llm]` 段 | 默认块改成本地端点；DeepSeek 块改为注释备用（或删除）；`[gui]` 段注释里的 dashscope 示例（约 L254）删或改成本地 VL 模型 | 不改则 `config init` / Docker 镜像默认仍是 DeepSeek。`Dockerfile:50` 把 example 烘进镜像，改 example 即改镜像默认 |
| M3 | 改 `config init` 最小 fallback | `nanomuse/cli.py:1443` | 同 M1 的本地端点三行 | 仅 example 文件缺失时才走这条分支，影响小但要同步 |
| M4 | （可选，修坑 1）`is_local_endpoint` 增加内网段 | `nanomuse/llm/factory.py:56-59` | 用 `ipaddress` 判断 `10/8`、`172.16/12`、`192.168/16` 也算本地；或直接在配置里填占位 key（零改代码，推荐） | 改代码后 `llm_ready` 对局域网无 key 端点返回 True |
| M5 | （可选）vision 加 halogen 前缀 | `nanomuse/llm/vision.py:66-68` | 在 `qwen` 分支前加 halogen 前缀判断（按实际 served 名定）；不加也能用（auto 实测） | 不改无功能损失 |
| M6 | 瘦身目录（激进：只留 4 条） | `nanomuse/llm/providers.json` | 删除 24 条云条目（bailian、deepseek、moonshot、zhipu、siliconflow、volcengine、minimax、openrouter、openai、anthropic、gemini、xai、groq、mistral、opencode-zen、opencode-go、standardcompute、api-route、cheaperinference、opper、atmorouter、uberllm、trustedrouter、synap），保留 `ollama`、`lm-studio`、`vllm`、`custom`。保守方案：只删不用了的，保留 deepseek/openai 等备用 | 删条目本身**不断编译**（目录是数据驱动）。但已有配置里 `provider = "deepseek"` 这类目录 id 会在 `config.py:52-68 check_provider` 抛 `ValueError: unknown provider`，**导致启动失败**——需同步迁移已有配置或在发布说明里写明 |
| M7 | 同步 4 份拷贝 | 生成命令 | `node scripts/providers-json.mjs`（源头改完必跑；CI `harness.yml:91-93` 的 `--check` 会拦 stale 拷贝）。拷贝在 `harness/dsh-nanomuse/assets/providers.json`、`android/.../assets/nanomuse/providers.json`、`android/src/ios/NanoMuse/Resources/providers.json`、`cloud/nanomuse_cloud/providers.json`。另有第 6 份内嵌在已提交的 web 构建产物 `nanomuse/server/static/assets/*.js`（minified）里 | 不跑则 CI 红，且桌面/手机/relay 仍显示旧目录 |
| M8 | 同步内置回退表 | `nanomuse/llm/catalogue.py:281-297` `_FALLBACK` | 删除 14 行云条目，保留 315-328 行的 ollama/lm-studio/vllm/custom 追加逻辑 | 不改则 `providers.json` 缺失/损坏时回退表又把 14 个云端点带回来（`catalogue.py:347-359 load_file`） |
| M9 | 同步 App 端预设 | `nanomuse/server/connections.py:78-198` `PROVIDERS` | 删除 `deepseek`、`kimi`、`qwen`（百炼）、`glm`、`doubao`、`minimax`、`openai`、`openai_responses`、`openrouter` 共 9 个预设，保留 `ollama`、`custom`；可新增一个 `halogen`/`vllm` 预设（`base_url` 填自建地址，`models` 填 served id，`key_optional: True`） | 不改则网页/App 的"添加模型"表单仍列官方云预设。删预设不断功能：`endpoints.py:239-249` `_unlisted` 只用它判断"已知厂商"来组织文案 |
| M10 | 重建 web 静态包 | `web/` → `nanomuse/server/static/` | `cd web && npm ci && npm run build` 后提交生成的 `static/`（AGENTS.md 要求构建产物随改动提交） | 不重建则网页端内嵌的仍是旧目录（显示旧条目），但后端 API 已是新的；不断编译 |

背景证据：
- `providers.json` 共 28 个 provider（version 1，updated 2026-10），**没有一个是 nanoMuse 官方自己的**（官方模型只通过 relay 提供，不在目录里）。第一条就是 `bailian`（:8-28，`base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"`，能力最全：chat/vision/image/video 四项全有）。本地/自建 4 条：`ollama`（`http://127.0.0.1:11434/v1`，auth `none`，默认 `qwen3:8b`）、`lm-studio`、`vllm`（`http://127.0.0.1:8000/v1`）、`custom`（`base_url: ""`）。
- 真正的默认模型是 DeepSeek：`config.py:85-88`（`provider = "openai"`、`model = "deepseek-flash"`、`base_url = "https://api.deepseek.com"`，注意无 `/v1` 后缀）；`config.example.toml` `[llm]` 段与 `cli.py:1443` 的 fallback 一致。
- relay 侧默认模型是另一个：`nanomuse/cloud.py:22-24` 的 `DEFAULT_CHAT_MODEL = DEFAULT_GUI_MODEL = "Qwen/Qwen3.6-27B-FP8"`；relay 菜单默认值 `cloud/nanomuse_cloud/config.py:255-263` 的 `DEFAULT_MODELS` 只有这一条。
- 自定义 base_url 完全支持：`config.py:70-82` 的 `resolve_provider`（自填的 `base_url` 优先于目录）；`llm/openai_chat.py:70-78` 直接 `AsyncOpenAI(api_key=settings.api_key or "EMPTY", base_url=settings.base_url or None, …)`——任意 `base_url` 都能接，无白名单、无域名校验。`tool_mode="auto"`（默认）时工具调用失败会自动降级为 prompt 模式（`config.py:96-104` 注释明确提到 Ollama/vLLM 场景），对 halogen/vLLM 友好。

**接 halogen/Ollama/vLLM 的两个坑**：
- 坑 1——局域网 IP 不算"本地"，无 key 会被判"模型未就绪"：`llm/factory.py:56-59` 的 `is_local_endpoint` 只认 `localhost`、`127.0.0.1`、`::1`、`0.0.0.0`、`host.docker.internal`；`:76-80` 的 `llm_ready` 对非本地端点要求 `api_key` 非空。`10.1.1.52` 这类局域网地址无 key 时 `llm_ready` 为 False，网页端会显示"先加模型"（`web/src/gate.ts` 的登录门也看 `llm_ready`）。但请求本身不依赖 key——`openai_chat.py:71` 无 key 时用 `"EMPTY"` 占位。**解法：`api_key` 填任意非空占位（如 `"local"`）即可**；halogen 不校验 key 就能通。改代码的解法见 M4。
- 坑 2——`provider = "custom"` 时 `base_url` 绝不能留空：`custom` 条目的 `base_url` 是 `""`，`resolve_provider` 会返回 `None`，`AsyncOpenAI(base_url=None)` 按 openai SDK 默认行为会指向 `https://api.openai.com/v1`。即配 `custom` 忘填地址会**静默连到 OpenAI 官方**。本条是 SDK 默认行为推断，**未在仓内实测**，验证法：`LLMSettings(provider="custom")` + `create_llm` 后打印 `client.base_url`。

牵连测试（改默认/删条目后必须同步改，否则红）：`tests/test_catalogue.py:12-17`（断言 28 条 + `bailian` capabilities；另有逐条测试）→ 删条目就删对应测试，数量改成保留后的条数；`tests/test_model_defaults.py`（`test_defaults_and_presets` 断言 `PROVIDERS["qwen"]`/`["openrouter"]`；`test_llm_ready_reads_the_vault_and_the_sign_in` 断言非本地无 key 不 ready——M4 不影响此断言，但应补一条 `10.x` 断言）；`tests/test_config.py:15-17,35,99-122`（断言默认端点是 DeepSeek 等）；`tests/test_cli.py`（1 处）、`tests/test_server.py`（6 处）、`tests/test_models_slots.py`（5 处）、`tests/test_endpoints_api.py`（1 处）、`tests/test_provenance.py`（2 处）、`tests/test_hub.py`（3 处）、`tests/test_memory_recall.py`（3 处）、`tests/test_avatar_studio.py`（6 处）、`tests/test_avatar_presets.py`（2 处）；`harness/dsh-nanomuse/tests/providers.test.mjs`（11 处，含 gemini `googleapis.com` 的 `listModels` 断言 L203-224）、`cloud-models.test.mjs`（11 处）、`cloud-providers.test.mjs`（2 处）、`video.test.mjs`（3 处）；`web/src/models.test.ts`（5 处，`BAILIAN` 常量 L5）；Android `ProviderCatalogueTest.kt`（5 处）、`ThinkingWireGoldenSnapshotTest.kt`（9 处）、`QwenThinkingTest.kt`、`DashScopeVoiceProviderTest.kt`（2 处）、`FallbackEntryIdentityTest.kt`、`OwnProviderProxyTest.kt`、`ProviderReachTest.kt`、`GuidanceTest.kt`、`ClaudeSignInTest.kt`；iOS `NanoMuseLogicTests.swift`（4 处）、`NanoMuseModelSlotsTests.swift`、`NanoMuseOpenCodeSessionTests.swift`；`tests/fixtures/goal_eval/tasks_v1_recording.json`（34 处 deepseek/dashscope 引用，录制回放夹具，应复核）；relay `cloud/tests/test_cloud.py`（15 处）、`test_accounts.py`、`test_allowance.py`（各 1 处）。

### 4.3 relay 默认上游（DashScope → 自建端点）

- 默认值：`UPSTREAM_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1`（`cloud/nanomuse_cloud/config.py:443`）、`UPSTREAM_KEY`（:444）。`DASHSCOPE_BASE=https://dashscope.aliyuncs.com/api/v1`（:447）仅供历史媒体代理用，chat 不走它。
- Chat 代理是纯 OpenAI 兼容转发，无 DashScope 专属逻辑：`api.py:1393`（`url = settings.upstream_base.rstrip("/") + "/chat/completions"`）、`:712-714`（`Authorization: Bearer {upstream_key}`）。无 key 时直接 503（`api.py:712-713`，`upstream_unconfigured`）。
- **接自有端点只需配三处（零改代码）**：`UPSTREAM_BASE=http://<本地地址>/v1`、`UPSTREAM_KEY=<任意非空字符串>`（**本地端点不要 key 也必须填非空，否则 503**）、`CLOUD_MODELS` 改成本地模型 id（`id`/`upstream` 填本地模型名，`for: ["chat","gui"]`，`recommended: true`）。`CLOUD_MODELS` 格式见 `cloud/.env.example:185`。**必须配**：否则 relay 默认菜单是 `Qwen/Qwen3.6-27B-FP8`，客户端请求该 id 而 halogen 不认识 → 上游 400/404。
- 注意事项：① `CHAT_DEFAULTS` 默认 `{"enable_thinking": false}`（见 S17）；② 流式强制 `stream_options.include_usage=true`（`api.py` 流式分支），vLLM/Ollama 均支持，halogen 是否支持**未确认**，需实测（不支持会表现为流式报错或 usage 缺失导致按估算 token 计费——`api.py` 有 `fallback_prompt_tokens` 兜底）；③ 目录探测默认开，建议 `CLOUD_CATALOG=0`（见 S13）；④ 媒体生成仍绑 DashScope（`api.py:1652-1943`），但 `CLOUD_MEDIA_ENABLED` 默认 `0`，关闭时媒体路由直接返回 410，不影响 chat；⑤ 计费价格是 DashScope 牌价（`config.py` 的 `ModelSpec` 的 `price_*`），接本地端点时 ledger 金额无意义，`ALLOWANCE_CNY=0` 后可忽略，或把 `CLOUD_MODELS` 里 `price_*` 置 0。
- 源码默认值改造（可选）：把 `config.py:443,447` 的 `_env` 默认值改成 halogen 地址或 `""`（空则 relay 启动时无上游 key，聊天报 `upstream_unconfigured`）。

### 4.4 Windows 桌面自动更新 feed（桌面唯一必须改指向的点）

- `harness/desktop/src/windows-updates.ts:33-38` 的 `SOURCES`：mirror `https://nanomuse.cn/dl/win/` + GitHub `https://github.com/nano-muse/nanoMuse/releases/latest/download/`，另有按版本 URL。检查先走官方镜像、失败 fallback GitHub Releases（:110-128）；下载前对候选版本二次校验 SHA-512（:241-266 的 `verifiedDownload`）。
- 构建侧 feed：`harness/desktop/electron-builder.yml:47` 全局 `publish: null`；`:84-86` 仅 Windows 段有 `publish: { provider: generic, url: "https://nanomuse.cn/dl/win/" }`。
- 改造二选一：(a) 改四个 URL 指向自建 feed，并改 `electron-builder.yml:86`，自建服务器托管 `latest.yml` + `.exe` + `.exe.blockmap` 三件套（SHA-512 必须与安装包一致）；(b) 直接删更新功能（见 U3）。mac/Linux 本来就没有自动更新，手动发安装包即可。

### 4.5 nudges 未登录回退官方（必改项）

- harness：`src/nudges.ts:86` 的 `NUDGES_ORIGIN = 'https://cloud.nanomuse.cn'`；`src/rooms.ts:724,728` 的 `fetch(\`${origin}/v1/nudges\`)`（`origin = status?.baseURL || NUDGES_ORIGIN`）。**未登录的 own-key 安装每天仍请求官方 relay 一次**。清理：删 `NUDGES_ORIGIN` 常量；`rooms.ts:718-728` 的 `refreshNudges()` 改为 `status?.baseURL` 为空时直接 `return`（不 fetch，用内置 `DEFAULT_NUDGES`）；调用点 `rooms.ts:1991`（后台刷新）与 `:2086`（强制刷新）不用动。
- Android：`community/Nudges.kt:199-203` 的 `GET baseUrl + /v1/nudges`（每天至多一次，`NanoMuseHome.kt:150` 触发），随 §4.1 的 `baseUrl()` 可空化改为 `baseUrl(...) ?: return null`；Android 无单独官方回退，改完即干净。
- Python：`nanomuse/nudges.py:220-234`，已有空 base_url 保护（:225），不用改。
- star 链接：`harness/src/nudges.ts:50-55` 的 `DEFAULT_NUDGES.star.url`、Python `nudges.py:34`、web `src/nudges.ts:41` 的 `github.com/nano-muse/nanoMuse` 改为自建项目地址；置空前需核对 `dueAsk` 对空 url 的处理（**未确认**）。
- iOS 同款：`NanoMuseNudges.swift:251`（未登录用 `defaultBase`）。
- 牵连测试：`android/.../test/.../community/NudgesPolicyTest.kt`（:18 用官方 GitHub URL 作测试数据，改期望即可）；`tests/test_nudges_runtime.py`（nudges 部分保留，只改期望 URL；注意该文件 :21 import `update` 模块，见 U1）。

### 4.6 其他硬编码外连（分类）

| 类别 | 域名/地址 | 位置举例 | 运行时必需？ | 处理 |
|---|---|---|---|---|
| 邮件页脚 | `https://nanomuse.cn/` | `cloud/nanomuse_cloud/senders.py:150`（纯文本版）、`:164`（HTML 版） | 否，**硬编码、无环境变量** | 改代码换成自建域名 |
| MCP OAuth client_uri | `https://nanomuse.cn` | harness `src/connectors.ts:48` 的 `CLIENT_URI`；Android `connectors/McpAuthDiscovery.kt:25` | OAuth 注册值，改域名需服务端配合验证 | 改自建域名 |
| GitHub API（skill 安装） | `api.github.com/repos/$user/$repo/contents/...` | Android `data/repository/SkillRepository.kt:854`；Python `nanomuse/skills/library.py:476-488` 的 `raw_skill_url()` | 用户主动从 GitHub 装 skill 时才调，repo 由用户指定，**不是官方耦合** | 可保留；想收紧可限制允许的域名前缀 |
| Google（Gemini/Antigravity） | `generativelanguage.googleapis.com`、`oauth2.googleapis.com`、`cloudcode-pa.googleapis.com` | Python `llm/catalogue.py:293`、`llm/providers.json:288`；Android `provider/gemini/GeminiProvider.kt:39`、`GeminiModelsApi.kt:51-54`、`auth/GeminiOAuthManager.kt`、`auth/AntigravityOAuthManager.kt` 等约 20 文件 | 仅用户选 Gemini/Antigravity 时才用 | 建议保留（不是 nanoMuse 官方耦合）；删则 Gemini 不可用。注意：**OpenMinis 上游子树不要动**——AGENTS.md 规定 `android/` 是上游子树、须保持可 pull，只允许新文件 + 带 `// nanoMuse:` 注释的单点修改；这些 Gemini 代码无 key/无登录不联网，留着零外连 |
| 模型供应商默认地址 | `api.openai.com`、`api.deepseek.com`、`openrouter.ai` 等 | `llm/catalogue.py`、`llm/providers.json`、web `providers.ts` | 用户配置的 provider 默认值，不用即无流量 | 随 §4.2 删条目 |
| ChatGPT 登录 | `chatgpt.com`、`auth.openai.com`、`api.openai.com/auth` | `nanomuse/llm/chatgpt.py:40-54`、`codex.py`、`chatgpt_proxy.py`；桌面/手机 ChatGPT 登录代码 | `provider = "chatgpt"` 时才用 | **建议保留代码、只不在配置里用**。想禁用可删三文件 + `factory.py:24-27` 的 chatgpt 分支 + `config.py` 的 `CHATGPT_PROVIDER` 相关校验——牵连面大（`tests/test_model_defaults.py` 有 chatgpt 登录测试、`nanomuse/chatgpt_cli.py`） |
| 搜索服务商 | `api.search.brave.com`、`api.tavily.com` 等 | `nanomuse/search.py:273-441` 各 1 处 | **默认是 DuckDuckGo（无 key）**，其余需 key 才启用 | 可保留（默认不连）；想收紧可删 keyed provider 实现，但牵连搜索测试，收益小 |
| 外链（纯链接，无运行时流量） | `nanomuse.cn` 各页 | web：`components/CommunityNotice.tsx:9`、`components/DataControls.tsx:11`、`screens/DevicesScreen.tsx:22`、`screens/AccountScreen.tsx:234`、`providers.ts:150`；Android：`NanoMuseCloud.kt:122` PRIVACY_URL、`ui/reach/ComputersScreen.kt:49`、`ui/cloud/CloudAccountScreen.kt:1061,1116,1158`、`ui/cloud/AllowanceWaysCard.kt:66,116`、`reach/Computers.kt:162`；harness：`desktop/src/main.ts:50,1904,1987`、`dsh-nanomuse/src/client/panels.ts:20` | 否 | 批量替换为自建域名或删除 |
| 镜像 LABEL | `github.com/nano-muse/nanoMuse` | `Dockerfile:13`、`cloud/Dockerfile:3` 的 `org.opencontainers.image.source` | 否，纯元数据 | 改成 `https://github.com/knsew/nanoMuse` 或删除 |
| PyPI / npm registry | `pypi.org`、`registry.npmjs.org` | `Dockerfile:19` 的 `PIP_INDEX_URL`；AGENTS.md 规定 lock 文件只许 `registry.npmjs.org` | 构建时依赖下载，运行期不连 | 构建机可走内网镜像（`--build-arg PIP_INDEX_URL=…` 已预留） |
| `firebase` / `gstatic` | — | **全仓 0 命中** | — | 无 Firebase SDK、无 Google 静态资源 |
| `sentry.io` | — | 仅 MCP 目录条目（见 D6），无 Sentry 错误上报 SDK、无 DSN | — | 见 D6 |
| 官方域名总量 | `nanomuse.cn` 623 行（docs 75 文件、harness 28、android 20、demo 18、cloud 14、nanomuse 9、web 7、tests 6、website 3、site 2）；`cloud.nanomuse.cn` 83 行；`github.com/nano-muse` 185 文件 1113 行（docs 85 文件占大头） | — | 真正运行时外连的代码点共 17 处（见附录），其余为文档/注释/测试/CHANGELOG | 文档/注释可批量替换为 fork 地址（纯文本，不影响编译） |

**无硬编码官方 API key**：grep `sk-` / `AKIA` / 字面量 key 赋值无命中。唯一的固定密钥是开发态 HMAC 回退值 `"nanomuse-cloud-dev-not-secret"`（`cloud/nanomuse_cloud/config.py:812-813`），仅 `CLOUD_SECRET` 为空时生效，生产必须设 `CLOUD_SECRET`。`config/config.example.toml` 里全是 `${VAR}` / `{{vault:NAME}}` 占位，无真实 key。

---

## 5. 建议的最小部署形态

### 5.1 容器（3 个，全部已有官方编排）

| 容器 | 镜像来源 | 作用 | 端口 |
|---|---|---|---|
| `relay` | `cloud/Dockerfile`（Python FastAPI + SQLite，单进程，uid 10001） | 账号、登录、hub（设备互联）、会话同步、模型代理 | 8787（经 Caddy 或直出） |
| `caddy` | `caddy:2`（官方镜像） | TLS 终止，Let's Encrypt 自动取证（公网部署时）；局域网部署可不要 | 80/443 |
| `app`（runtime） | 根 `Dockerfile`（Python runtime，uid 1000） | web app + hands（浏览器/电脑操作） | 8788（与 relay 错开；默认也是 8787，同机必须错开） |

可选第 4 个：`daemon`（同一 runtime 镜像，`daemon --interval 3600`，定时推进 goals，无 UI）。`muse`（交互终端 `chat`）是 `docker compose run` 的一次性服务，不常驻。

部署命令（官方脚本已支持）：

```bash
git clone https://github.com/knsew/nanoMuse.git && cd nanoMuse
bash scripts/self-host.sh --local --with-runtime   # relay 8787 + web app 8788，本机 http
# 公网：bash scripts/self-host.sh（交互问域名、邮箱、验证码通道、上游、管理员密码）
```

注意：`scripts/self-host.sh` 第 4 步上游地址是开放填空，默认才填 DashScope（`set_kv UPSTREAM_BASE "${UP_BASE:-https://dashscope.aliyuncs.com/compatible-mode/v1}"`，L206-216），填 halogen 地址即可。`--with-runtime` 时 relay 与 runtime 以 `relay:8787` 在同一 docker 网络互访，runtime 的 `.env` 会被写入 `NANOMUSE_CLOUD_BASE_URL=http://relay:8787`（不覆盖已有值）。

### 5.2 哪些目录可从构建里砍掉

| 目录 | 砍不砍 | 理由 |
|---|---|---|
| `site/`、`website/`、`demo/` | 砍（从 fork 里删除，或至少不进构建） | 落地页/文档站/演示网关，部署用不上。根 `Dockerfile` 的 `.dockerignore:28-35` 已排除 `android/`、`harness/`、`cloud/`、`web/`、`website/`、`site/`、`assets/`、`scripts/`、`tests/`、`docs/`，镜像里本来就没有这些 |
| `cloud/deploy/nanomuse-hk/` | 砍（见 R12） | 官方生产部署脚本 |
| `android/`、`harness/` | 保留源码（客户端要重编译），但不进 Docker 镜像 | `.dockerignore` 已排除；Android/iOS/桌面是另外的构建产物 |
| `web/` | 保留源码（构建产物 `nanomuse/server/static/` 已提交，随 runtime 镜像走） | 根 `Dockerfile` 只 COPY `pyproject.toml`、`README.md`、`LICENSE`、`nanomuse/`、`config/config.example.toml`；`web/` 源码不进镜像，但改 web 后要重建 `static/` 并提交 |
| `cloud/console/` | 保留源码（构建产物 `nanomuse_cloud/console/admin/` 已提交，随 relay 镜像走） | `cloud/.dockerignore` 排除 `console/` 源码、`.env`、`data/`、`tests/`；改控制台后要重编提交 |
| `docs/`、`tests/`、`scripts/`（除 `self-host.sh`） | 保留源码，不进镜像 | `.dockerignore` 已排除 |

### 5.3 部署配置要点（`.env` / `config.toml`）

`cloud/.env`（relay）：

```env
CLOUD_DOMAIN=<自建域名>            # 公网；局域网用 --local 模式 + PUBLIC_BASE=http://<局域网地址>:8787
PUBLIC_BASE=https://<自建域名>
CLOUD_SECRET=<openssl rand -hex 32>
CLOUD_ADMIN_TOKEN=<openssl rand -hex 32>
UPSTREAM_BASE=http://<halogen地址>/v1
UPSTREAM_KEY=local                  # 本地端点不要 key 也必须填非空，否则 relay 聊天报 503
CLOUD_MODELS=[{"id":"<halogen模型id>","name":"...","upstream":"<halogen模型id>","input_modalities":["text","image"],"for":["chat","gui"],"recommended":true,"recommended_for":["chat","gui"],"price_in":0,"price_out":0}]
CHAT_DEFAULTS={}                    # 先用空对象起步实测，见 S17
SIGNUP_OPEN=0
ALLOWED_IDENTIFIERS=<自己邮箱>
CODE_SENDER=log                     # 或 smtp（自建邮箱）
ALLOWANCE_CNY=0
INVITE_BONUS_CNY=0
RECORD_TURNS=0
GITHUB_COLLECT=0
CLOUD_GEOIP=0
CLOUD_CATALOG=0                     # 本地端点建议关目录探测
CLOUD_MEDIA_ENABLED=0               # 默认已是 0
IMPROVE_DEFAULT=0                   # 默认已是 0
```

根 `.env` + `config/config.toml`（runtime）：

```toml
# config.toml [llm]
provider = "openai"
model = "<halogen served id>"
base_url = "http://10.1.1.52:8000/v1"
api_key = "local"                   # 占位，见坑 1
# config.toml [cloud]
base_url = "<自建 relay 地址>"       # 不要 relay 则 required = false
required = true
# config.toml [server]
update_check = false
```

另注意：`cloud/data` 目录必须属于 uid 10001（`mkdir -p data && sudo chown -R 10001:10001 data`，`self-host.sh` 会自动做）；根 compose 的 `./config/config.toml` 是只读挂载，宿主机须先有该文件，否则 Docker 会建个目录顶替导致容器起不来（`self-host.sh` 的 `ensure_runtime_config` 就是防这个的）。备份：relay 的 `cloud/data/cloud.db`（SQLite，需用 SQLite backup API 在运行容器内取一致副本，见 `docs/self-hosting.md`）+ `CLOUD_SECRET` + `MEDIA_DIR` 下的文件，三者一起备份才可恢复。

### 5.4 客户端指向自建 relay（不改代码也能用）

- **Android**（0.1.38+）：登录屏"Use a different server"填 relay 地址，`Check` 调 `/healthz` 校验，`Use this server` 持久化；`https://` 在外网必需，`http://` 仅接受内网地址（`10.x`、`172.16-31.x`、`192.168.x`、`localhost`、`.local`、`.ts.net`）。设置 → 账号可改服务器（先登出）。
- **iOS**：登录页同样入口，规则相同。
- **桌面**：`cordis.patch.yml` 的 `baseURL`，或环境变量 `NANOMUSE_CLOUD_URL`。
- **终端 runtime / web app**：`NANOMUSE_CLOUD_BASE_URL` 或 `[cloud] base_url`。
- 同一账号的所有设备必须指向同一个 relay（hub 与会话都在 relay 上）。

---

## 6. 未确认项（动手/部署时逐项验证）

| # | 内容 | 验证方法 |
|---|---|---|
| 1 | Ollama/halogen 是否容忍顶层 `enable_thinking` 字段 | `CHAT_DEFAULTS={}` 与默认各跑一次 chat，对比上游是否 400。缓解：先用 `{}` |
| 2 | halogen 是否支持 `stream_options.include_usage` | 对 halogen 跑一次流式 chat，看是否报错或 usage 缺失 |
| 3 | 客户端是否容忍 `/v1/me` 缺 `invite` / `nudges` 字段 | 看 Android 与桌面插件解析 `/v1/me` 的代码是否用可选字段。缓解：先保留字段返回空结构 |
| 4 | 客户端在 `/v1/turns` 路由整个删掉后的行为 | 看 Turns 页对 404 的处理；或先做 R9 a 档观察 |
| 5 | `db.delete_account` 是否已清 turns 行 | `grep -n "def delete_account" cloud/nanomuse_cloud/db.py` 后读函数体；若未清，R10 b 档删除时需补 |
| 6 | iOS 后台/被杀后 Hub WebSocket 是否还能收审批推送 | 代码里未找到 `BGTaskScheduler`/`BGAppRefreshTask`/`beginBackgroundTask` 保活。验证：iOS 真机切后台/杀掉，从电脑发审批看手机是否收到本地通知；或通读 `BackgroundKeepAliveManager.swift` 全文并查 `Info.plist` 的 `UIBackgroundModes` |
| 7 | 桌面端 crashpad 目录实地确认 | 代码层面已确认无 `crashReporter.start` 调用（低风险）。验证：打包后的 Windows 机器上跑应用，检查 `%APPDATA%/nanoMuse/Crashpad` 目录是否存在及有无出站连接 |
| 8 | `provider = "custom"` 且 `base_url` 为空时是否静默回落 `api.openai.com` | `LLMSettings(provider="custom")` + `create_llm` 后打印 `client.base_url` |
| 9 | halogen 的 served 模型 id | `curl http://10.1.1.52:8000/v1/models` |
| 10 | Android 语音默认是否走 DashScope | grep `dashscope` 看 `android/.../speech/` 与 `VoiceProviderFactory`；`DashScopeVoiceProviderTest.kt` 未细读 |
| 11 | `minis-mcp-cli/main.py` 里的 googleapis 用途 | `grep -n googleapis android/src/android/app/src/main/assets/default_mount/usr/local/lib/minis-mcp-cli/main.py` |
| 12 | harness `relay.ts` 的 `hasBlob`/`putBlob`/`postTurn`/`postEvents` 是否仅 OwnTurns/事件队列调用 | 动手时 grep 全仓库确认后再删（见 T2） |
| 13 | `harness/dsh-nanomuse/src/record.ts` 除 `cloud.ts` 外有无引用 | 动手时 grep `from './record` 确认（见 T2） |
| 14 | dsh 更新信息（`snapshot.update`）的 UI 渲染位置 | `client/panels.ts`、`client/index.ts` 内无引用；动手时 grep 全仓库 `snapshot.update`（见 U2） |
| 15 | `harness/desktop/src/windows-network-cost.ts` 与 `electron-updater` 是否仅被 `windows-updates.ts` 引用 | 动手时 grep 确认（见 U3） |
| 16 | `desk.ts` 的 `compareVersions`/`pickAsset` 在 `cloud.ts:505` 之外有无调用 | 动手时 grep 确认（见 U2） |
| 17 | `UpdateChecker` 在 About 页的调用点 | main 源码内仅见 Debug RPC 调用；`UpdateCheck.kt` 注释称 About 页用它下载 APK（见 U4） |
| 18 | `DEFAULT_NUDGES.star.url` 为空时 `dueAsk` 是否还弹 star 提示 | 核对 `dueAsk` 对空 url 的处理（见 §4.5） |
| 19 | relay 端关掉 contribute 后是否真的丢弃训练视图 | 看 relay `service.py:3039` 的 `keep_sample` 与 `service.py:155` 的 `_training_view`（训练样本写入仅 `caller.contribute` 为真时，代码层面已确认；实测验证为佳） |
| 20 | `sentinel/gate.py` `_note()` 与 `phone/operator.py` `_note_step`/`_note_outcome` 的调用点数量 | 动手时 grep `_note(` / `_note_step\|_note_outcome` 确认（见 T1） |
| 21 | `nanomuse/llm/session.py` 顶部 `_cloud_host` 初始值对空 base_url 时 `is_cloud()` 行为的影响 | 动手时核对（`session.py:142` 的 `_cloud_host = "cloud.nanomuse.cn"` 默认值；`set_cloud_base` 会在 hub 启动时用实际地址覆盖） |
| 22 | Android OpenMinis 上游（`com.openminis.*`）的其他云 provider（ChatGPT/Claude/Gemini 登录等） | 本次 grep 重点是 nanoMuse 自有代码与目录，上游子树未逐项审计；按 AGENTS.md 上游子树不宜大改，且无 key/无登录不联网 |

---

## 7. 逐项证据附录

### 7.1 默认 relay/服务端地址（17 处运行时外连代码点）

| 位置 | 代码 | 是否运行时必需 | 替换方案 |
|---|---|---|---|
| `nanomuse/config.py:645` | `CloudSettings.base_url` 默认 `"https://cloud.nanomuse.cn"` | 是默认 relay 地址；可改 | 改自建地址；或 `required`（L649，默认 `True`）改 `False` 则完全不要 relay |
| `nanomuse/llm/session.py:142` | `_cloud_host = "cloud.nanomuse.cn"`（默认值） | 否，仅用于 `is_cloud()` 判断来源标记；`session.py:219-225` 的 `set_cloud_base` 会在 hub 启动时用实际地址覆盖 | 改 `""` 或自建 host；不改也无外连（只是字符串比较）。零风险 |
| `nanomuse/server/update.py:29-31` | `INDEX_URL`、`RELEASES_API` | 否，版本检查 | 见 §2.3 |
| `harness/dsh-nanomuse/src/cloud.ts:108,113` | `OFFICIAL_CLOUD_ORIGIN`，`relayMediaEnabled()` | 否，纯字符串比较，不联网 | 可保留；去痕迹可改成与自建地址比较或直接返回 true |
| `harness/dsh-nanomuse/src/cloud.ts:138` | `Config.baseURL` 默认官方 | 是桌面插件默认 relay；可改 | 改自建地址 |
| `harness/dsh-nanomuse/cordis.patch.yml:83` | `baseURL: process.env.NANOMUSE_CLOUD_URL \|\| 'https://cloud.nanomuse.cn'` | 同上 | 改默认值或确保环境变量指向自建 |
| `harness/dsh-nanomuse/src/nudges.ts:86` + `rooms.ts:724` | `NUDGES_ORIGIN`，未登录时从官方拉文案策略 | 否，**隐蔽外连** | 见 §4.5 |
| `harness/desktop/src/proxy.ts:13,55-60` | `DEFAULT_RELAY_HOST`，放进 NO_PROXY 列表 | 否，仅代理绕行名单 | 改自建 host 或删掉该项 |
| Android `NanoMuseCloud.kt:49,283,287` | `DEFAULT_BASE` | 是 Android 默认 relay；可改 | 改自建地址 |
| Android `MediaModels.kt:20` | `isPublicCloud()` | 否，字符串比较；自建 relay 本来就不受限 | 可保留；去痕迹则删判断 |
| Android `ImageGen.kt:81` | `baseUrl != DEFAULT_BASE` 才走自建媒体路径 | 否 | 随 DEFAULT_BASE 一起改 |
| iOS `NanoMuseCloud.swift:118,154,158,260` | `defaultBase`；`usesOwnRelay` | 是 iOS 默认 relay；可改 | 改自建地址 |
| `config/config.example.toml:364` | `[cloud] base_url` 默认官方 | 示例配置 | 改自建地址 + `required = false` |
| `demo/showcase/gateway/showcase_gateway/config.py:284` | `WEB_RELAY_URL` 默认官方 | 仅演示网关 | 改或删（demo 整目录可删，见 D3） |
| `cloud/docker-compose.yml:18` | 注释里举例 `CLOUD_DOMAIN` 用官方域名 | 注释 | 改注释示例 |
| `cloud/deploy/nanomuse-hk/*`（整目录） | 官方生产部署脚本 | 否 | 整目录删（见 R12） |
| `harness/dsh-nanomuse/src/hub.ts:113`、`relay-proxy.ts:35`、`relay.ts:310`、Android `RelayAddress.kt:72`、`VideoGen.kt:51` | 注释/docstring 里的示例地址 | 否 | 顺手改注释即可 |

### 7.2 遥测证据

- 发到当前配置的 relay：`telemetry.py` 本身无 URL，所有发送走注入的 `CloudClient`：`POST /v1/events`（`cloud.py:228-233`，`telemetry.py:186` 调用）、`POST /v1/turns`（`cloud.py:220-226`，`telemetry.py:235` 调用）、`HEAD/PUT /v1/blobs/{sha256}`（`cloud.py:235-257`，`telemetry.py:262-263` 调用）。
- 发什么：①客户端事件（Sentinel 判定、审批结果、停止原因、点赞/点踩、hands 步骤，只含 code/数字/布尔，`event()` :107-138 会过滤非标量值）；②自有 key 的**完整 turn**（`turn_body()`，含全部 messages 与 assistant 回复、用量、provenance），图片超 2MB 先走 blob 上传。事件队列 1000 条、turn 队列 50 条，内存内，5 秒批量 flush（:38-42）。
- 默认开、无开关：见 §2.2 背景证据。

### 7.3 更新检查证据

见 §2.3 背景证据（四处独立实现，`update.py:29-32`、`desk.ts:517-519`、`UpdateCheck.kt:34-36`、`windows-updates.ts:38-41`）。

### 7.4 崩溃上报/推送证据

- Android：ACRA 仅 core，无网络发送器（`android/src/android/app/build.gradle.kts:317-320` 注释明确"acra-core only (no http sender, no network permission)"）；初始化 `MinisApp.kt:220-236`；发送器只写本地文件 `crash/CrashFileReporter.kt:24-31`（`filesDir/logs/crash-<stamp>.log`）；Native 崩溃同样本地 `crash/NativeCrashHandler.kt:12-15`；1 小时内崩溃 ≥2 次才弹窗让用户**手动**分享日志（`CrashFrequencyDetector.kt:33`，注释"local fallback for Crashlytics (#458)"）。
- iOS：`android/src/ios/Diagnostics/CrashReporter.swift:3,9`（`import MetricKit`）；报告只落本地 `Library/Logs/`（:14-18，:465、:529、:1030 均为本地写盘）；全文件无 `URLSession` / `upload` / `http` 调用。SPM 依赖只有 4 个，无上报类（`Minis.xcodeproj/project.pbxproj:3793-3819`）。
- 桌面：`harness/desktop/package.json` 的 `dependencies` 只有 `@computer-use/nut-js` 与 `electron-updater`；`harness/dsh-nanomuse/package.json` 只有 `@deepseek-ai/dsh-*`、`@deepseek-ai/cordis`、`react`、`esbuild`、`typescript` 等，无上报依赖。grep `crashReporter|crashpad` 全仓零命中。
- 推送：Android 依赖清单 `build.gradle.kts:211-339` 无任何 `com.google.firebase` / `com.google.android.gms`；grep `FirebaseMessaging|FCM|MessagingService|RemoteMessage` 零命中；全仓无 `google-services.json`，也无 `GoogleService-Info.plist`；`AndroidManifest.xml` 无 Firebase 服务/receiver 声明。iOS `Minis.entitlements` 无 `aps-environment`；grep `registerForRemoteNotifications|didRegisterForRemoteNotifications|didReceiveRemoteNotification|deviceToken` 零真实命中。
- 实际跨设备"推送"机制：Hub WebSocket。Android `hub/Hub.kt:96-98`（base URL 的 `https://`→`wss://` + `/v1/hub`）、`HubClient.kt:167`（OkHttp `newWebSocket`）、`HubService.kt:29,43`（前台服务保活）；iOS `NanoMuseHub.swift:1-3` 注释"one outbound WebSocket to the relay"、`:179-188`（`wss://` + `/v1/hub`）；审批推送流程 `docs/hub.md:173-178,316-325`（relay 0.31 起支持无 id 的 `event` 帧作 push，限速 30 条/分钟/设备）；relay 服务端 grep `apns|fcm|firebase|push notification|device token|webpush` 零命中。
- Android 其他第三方库（均无上报）：Shizuku `api` + `provider:13.1.5`（本地 binder IPC，无网络）；RealTimeCutVADLibraryForAndroid `1.0.5@aar`（本地 Silero VAD + ONNX，离线）；rclone `.aar`（自编译产物，备份远端 SMB/WebDAV/SFTP/S3/FTP）；Haze `1.6.10`（纯 UI 毛玻璃）；Coil、markdown-renderer、reorderable、snakeyaml（纯本地功能库）。全仓 grep `com.google.android.gms|com.google.firebase|FirebaseApp` 零命中——应用不依赖 GMS，可在无 GMS 设备/侧载运行。

### 7.5 `cloud/` 目录结构（后端约 18,490 行 Python）

| 模块 | 行数 | 职责 |
|---|---|---|
| `service.py` | 3,956 | 业务核心：注册/登录、额度、邀请、record、统计 |
| `db.py` | 3,744 | SQLite：账号、ledger、turns、事件、迁移 |
| `api.py` | 2,754 | 全部 HTTP 路由（`/v1/*`、`/v1/admin/*`、hub） |
| `analytics.py` | 1,744 | 运营统计聚合 |
| `config.py` | 819 | 全部环境变量配置（`Settings`） |
| `stats.py` | 767 | 控制台统计 JSON/CSV |
| `hub.py` | 759 | 多设备 WebSocket 中枢（**核心，必须保留**） |
| `sync.py` | 644 | 跨设备会话同步（**核心，必须保留**） |
| `record.py` | 365 | 全量对话记录 + 媒体文件存储 |
| `catalog.py` | 355 | 上游 `/models` 目录 + 模型探测 |
| `senders.py` | 334 | 验证码发送：log / SMTP / 阿里云短信 |
| `geo.py` | 333 | ip2region 地域查询 |
| `controls.py` | 306 | 运营开关 + 阈值通知 |
| `provenance.py` | 284 | turn 来源追踪 |
| `github_stats.py` | 280 | GitHub stars/forks/下载量采集器 |
| `client.py` | 219 | 客户端识别（UA、IP） |
| `nudges.py` | 176 | star 索要策略 |
| `turnview.py` | 172 | turn 展示布局 |
| `providers.py` + `providers.json` | 164 | 自带 key 指引 |
| `__main__.py` | 152 | 启动入口 + `admin controls` CLI |
| `identifiers.py` | 114 | 手机号/邮箱解析与规范化 |
| `crypto.py` | 39 | 标识符 AES-GCM 加解密 |

### 7.6 `config/` 目录

只有一个文件：`config/config.example.toml`（19,395 字节）。没有 `config.toml`（被 `.gitignore` 忽略、`.dockerignore:19` 排除出镜像构建上下文；首次使用由 `nanomuse config init` 从 example 拷贝，`cli.py:1430-1448`）。含官方地址的位置：`[llm]` 默认 DeepSeek、`[llm]` 注释里的 `provider = "bailian"` + `${DASHSCOPE_API_KEY}` 示例、`[gui]` 注释里的 dashscope 示例（L254）、`[image]`/`[video]` 注释块的 `provider = "bailian"` 示例、`[server]` 的 `update_check = true`、`[cloud]`（L364 起）的官方 `base_url` + `required = true`。`[sentinel] egress_allowlist`（duckduckgo.com、wikipedia.org、github.com、pypi.org 等）只是放行名单，不是主动外连，可保留。`[connectors.email]` 的 Gmail IMAP/SMTP 默认 `enabled = false`，不启用不连。

### 7.7 Dockerfile / docker-compose 形态

- 根 `Dockerfile`（65 行，`python:3.12-slim`）：L13 LABEL 官方 source；L19 `PIP_INDEX_URL`（可 `--build-arg` 换内网镜像）；L35-37 只 COPY `pyproject.toml`、`README.md`、`LICENSE`、`nanomuse/`、`config/config.example.toml`；L50 把 example 烘成镜像默认配置；L56-65：`VOLUME ["/data", "/workspace"]`、`EXPOSE 8787`、HEALTHCHECK 查 `/api/health`、默认 `CMD ["serve", "--host", "0.0.0.0", "--no-qr"]`；可选 `WITH_BROWSER=1` 加 Chromium（约 +400MB）；运行用户 `muse`（uid 1000）。
- 根 `docker-compose.yml`（3 个服务，同一镜像，无 relay、无数据库服务——SQLite 在 named volume 里）：`muse`（`chat`，交互终端）、`app`（`serve`，端口 8787，`restart: unless-stopped`）、`daemon`（`daemon --interval 3600`）。共用挂载：`nanomuse-data:/data`、 `./workspace:/workspace`、 `./config/config.toml:/app/config/config.toml:ro`。
- `cloud/Dockerfile`（25 行）：只 COPY `pyproject.toml`、`README.md`、`nanomuse_cloud/`；运行用户 `relay`（uid 10001）；`CLOUD_DB=/srv/nanomuse-cloud/data/cloud.db`；HEALTHCHECK 查 `/healthz`；默认 `nanomuse-cloud --host 0.0.0.0 --port 8787`。
- `cloud/docker-compose.yml`（2 个服务）：`relay`（`./data` 挂载，`expose 8787` 不直接发布）+ `caddy`（`caddy:2`，发布 80/443，按 `CLOUD_DOMAIN` 自动申请 Let's Encrypt 证书）。
- `cloud/docker-compose.local.yml`（override）：不要 TLS 时用——`relay` 单独跑在 `127.0.0.1:8787`（`RELAY_BIND`/`RELAY_PORT` 可改），`PUBLIC_BASE` 告诉 App relay 地址，`CODE_SENDER=log`；`caddy` 被移到 `tls` profile 下不启动。

### 7.8 验证命令（按 AGENTS.md，改完对应领域跑对应命令）

```bash
# Python runtime
ruff check nanomuse tests scripts && ruff format --check nanomuse tests scripts
mypy
python -m pytest -q -m "not live"
# Relay
cd cloud && pip install -e ".[dev]" && ruff check . && ruff format --check . && pytest
cd cloud/console && npm ci && npm run check && npm run build
# Web console
cd web && npm ci && npm run check && npm run build
# Desktop plugin
cd harness/dsh-nanomuse && pnpm install --frozen-lockfile && pnpm build && pnpm typecheck && pnpm test
# Desktop shell
cd harness/desktop && npm ci && npm run typecheck && npm test
# 目录同步检查
node scripts/connectors-json.mjs --check
node scripts/providers-json.mjs --check
# Android
cd android/src/android && ./gradlew :app:testDebugUnitTest --tests 'io.github.nanomuse.*' --console=plain --no-daemon
```

### 7.9 删完复查

每项删完后 grep 复查残留——预期为零，例外只有有意保留的项（SkillRepository 的用户指定 repo GitHub API、测试示例值、保留的云 provider 条目）：

```bash
grep -rn "nanomuse.cn\|nano-muse\|api.github.com" --include='*.py' --include='*.ts' --include='*.tsx' --include='*.kt' --include='*.swift' nanomuse cloud web harness android/src | grep -v tests | grep -v node_modules
```

---

*报告完。本报告只写查实的结论；不确定的项已在 §6 列出并附验证方法。*
