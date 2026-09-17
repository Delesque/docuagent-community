# 发布检查单（社区版）

本文件记录从源码到公开分发的每一步、对应命令和判定标准。公开仓库中不含本文件以外的内部文档。

## 0. 当前状态

| 项目 | 值 |
| --- | --- |
| 版本 | `0.1.0`（`core.py`、`frontend/package.json`、`desktop/package.json` 及两个 lock 文件一致） |
| 发布快照 | `.release/community-product-0.1.0-20260917`（354 文件，**不是预览导出**，无 `PREVIEW-ONLY.txt`，清单 SHA-256 `F8E1F35F3116DCEE7EC1393D94707A851674A0FB8D813ECF2BC833562031C7AB`） |
| 发布闸门 | `license_approved=true`、`publication_approved=true`（已开闸） |
| 源码提交 | 开闸与升版本提交见开发树 `git log`；公开仓库同步后就地打包 |
| 公开仓库 | `Delesque/docuagent-community`（**已公开**），全新历史、无内部提交，`main` = `e07888a30b`，356 个文件 |
| CI | GitHub Actions run `35229671709` 通过：后端 `795 passed / 6 skipped / 158 subtests`，前端 `72 files / 476 tests`，Vite 构建通过 |
| 发布形态（已定） | **只发源码**：`main` 分支 + tag，Release 不挂安装包附件 |
| 本地验证制品（不入库） | 安装包 `DocuAgent-Setup-0.1.0-x64.exe` SHA-256 `022313F6D150728A8EBA7E3AA394A9F4E22C121EA5447AC069DF3B2C1D4D639F`（107.1 MB）；便携版 `DocuAgent-Portable-0.1.0-x64.exe` SHA-256 `CFF1909E4A237A311CBE6253ECA609F3B7A85F0C964912A5A8CD19EB5FE14520`（106.9 MB） |

## 0.1 本次发布验证记录

- **真实模型端到端验收通过（2026-09-17）**：以正式快照起真实服务，用 DeepSeek 官方 deepseek-flash 从空目录交付了一个命令行书签管理器。访谈、架构生成、架构预检（拦下模型写错的验证命令并要求修订）、初始化、4 轮代码生成、逐条应用与验证、文档交付门全部由产品完成；4 个模块全部 erified，docs_in_sync=true、delivery_status=ready。独立复验 31/31：交付物自带的 144 个测试全部通过，CLI 的新增/列表/搜索/删除/重复 URL/空标题/空 ID/损坏文件行为符合验收标准，损坏的存储文件未被覆盖；用量 64 次调用 / 1,330,209 token，模块级与功能级归属完整。
- **可用性验证两份产物各 38/38**：以真实服务驱动 HTTP API，覆盖启动与静态资源、版本与会话保护、密钥不外泄、项目打开与扫描、终端策略、Git 与快照、用量维度、匿名统计与同意、持久化与错误处理；源码快照用系统 Python 运行，打包版用随包 python-runtime 且在 PATH 无 Python 的条件下运行。

- 快照哈希复验：364 项全部存在、哈希一致、无清单外文件（`python .release/verify_snapshot.py <dir> --release` → `RESULT: OK`；该检查同时确认非预览导出没有 `PREVIEW-ONLY.txt`）。
- 快照内独立验证：后端 `786 passed / 1 skipped / 155 subtests`，前端 `72 files / 476 tests`，`tsc -b` 与 `vite build` 通过，`npm audit --omit=dev` 为 0 vulnerabilities。
- 桌面成品：从该快照重新构建安装包与便携版，二者在 `PATH` 只含 `C:\Windows\System32;C:\Windows`（机器上没有任何 Python）时启动正常——解包构建 `/api/health` 返回 `{'ok': True, 'version': '0.1.0'}`，首页与 `/api/telemetry`、`/api/token-usage` 正常；`DOCUAGENT_DESKTOP_SMOKE=1` 烟测两个产物退出码均为 0。
- 历史记录：首次 GitHub Actions 运行暴露 8 个 Windows 本地跑不出来的跨平台失败（验证夹具使用 Python `-c` 内联、文件索引把相对路径交给 `as_uri()`），已在 `7bee906` 修复并复跑通过；Linux 侧另有 6 个 Windows 专用用例被跳过。

## 1. 发布前必须完成（已完成的打勾）

1. ✅ **开发布闸门**：`release/community-manifest.json` 的 `publication_approved=true`，已按非预览方式重新导出。
2. ✅ **升版本**：四个声明与发布清单统一为 `0.1.0`。
3. ⬜ **已转公开**：2026-09-17 执行 `gh repo edit Delesque/docuagent-community --visibility public --accept-visibility-change-consequences`；匿名访问 README/LICENSE/源码与 Release 均已验证。
4. ⬜ **已打 tag 并发 Release**：注签 tag `v0.1.0`（对象 `bac6b6e9cb`，指向 `e07888a30b`），Release 无附件，note 见 https://github.com/Delesque/docuagent-community/releases/tag/v0.1.0 。

首发之后再处理（不阻断源码发布）：

5. **代码签名**：安装包未签名会触发 SmartScreen；发布桌面包附件之前必须有证书，或在 note 中明示未签名并附上 SHA-256。
6. **桌面包附件**：`docuagent/desktop/package-lock.json` 已在白名单内；附件发布时把两个产物的 SHA-256 写进 Release note。

## 2. 导出与验证

```powershell
# 正式导出（publication_approved=true 时才能不带 --preview）
python tools/export_community.py --output .release/community-product-candidate-<version>

# 清单哈希与白名单复验（干净副本）
python .release/verify_snapshot.py .release/community-product-candidate-<version> --pristine

# 快照内独立验证
cd .release/community-product-candidate-<version>/docuagent
$env:PYTHONPATH="docuagent"; python -m pytest -q            # 期望 786 passed / 1 skipped
cd frontend; npm ci; npm test -- --run                      # 期望 70 files / 463 tests
npx tsc -b --pretty false; npm run build
npm audit --omit=dev                                        # 期望 0 vulnerabilities
```

判定：清单哈希与记录一致、无清单外文件、上面五项全部通过。

## 3. Windows 打包与烟测

打包机需要能访问外网；本机 Node 进程直连受限时，用镜像与本地缓存（见下）。

```powershell
cd <snapshot>\docuagent\desktop
npm install
npm run dist                 # 前端构建 + electron-builder --win nsis portable
cd ..
python .release\smoke_desktop.py <artifacts-dir>   # DOCUAGENT_DESKTOP_SMOKE=1，期望退出码 0
Get-FileHash <artifacts-dir>\DocuAgent-Setup-*.exe -Algorithm SHA256
```

镜像与缓存前提（本机已验证可行）：

```powershell
$env:ELECTRON_MIRROR="https://registry.npmmirror.com/-/binary/electron/"
# winCodeSign-2.6.0.7z 需提前解压到 %LOCALAPPDATA%\electron-builder\Cache\winCodeSign-2.6.0
# 并补写同名 .state 文件（state=complete）
```

## 4. 遥测客户端验证

```powershell
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
python .release/telemetry-collector/verify.py     # 期望 40/40
```

覆盖：未配置端点无请求、未同意无请求、同意后立即上传、24 小时批量、失败保留、重复上传幂等、collector 侧凭据拒绝。

## 5. 公开仓库与 GitHub 侧

```powershell
# 已在本地准备好的干净仓库（全新历史，无内部提交）
cd .release\community-public-repo-20260917

gh repo create Delesque/docuagent-community --private --source . --remote origin --push
# 确认 CI 在两个 job 上均为绿后，决定是否公开：
gh repo edit Delesque/docuagent-community --visibility public --accept-visibility-change-consequences
```

### 网络受限时的推送方式

本机 `github.com:443` 不通（git push 报 `Connection was reset`），但 `api.github.com` 与 `gh` 可用，因此首次推送走 Git Data API：

```powershell
python .release/push_via_api.py .release/community-public-repo-20260917 Delesque/docuagent-community --branch main --bootstrap
```

脚本会先通过 Contents API 建首个提交打破“空仓库不能建 blob”的 409，再上传全部 blob 并建树；**新提交的 tree SHA 必须与本地 `HEAD^{tree}` 完全一致**，不一致会中止并且不更新 ref。当前状态：远端 `main` = `128501141c…`，tree = `2ad0062fa8…`，与本地 `8f745ef` 的 tree 相同，因此远端文件内容与本地提交逐字节一致。因为这个绕行，远端历史是 2 个提交（bootstrap + 首次发布），而本地是 1 个根提交；网络恢复后若想让远端历史与本地一致，可在本地仓库执行 `git push --force origin main`。

CI 判定：`Backend tests` 与 `Frontend tests + build` 两个 job 通过；若失败，先在私有仓库修好再公开。

## 6. 发布 Release

```powershell
git tag -a v0.1.0 -m "DocuAgent community 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --title "DocuAgent 0.1.0" --notes-file RELEASE_NOTES.md `
  DocuAgent-Setup-0.1.0-x64.exe DocuAgent-Portable-0.1.0-x64.exe
```

Release note 必须包含：安装包与便携版的 SHA-256、系统要求（Windows 10/11 x64、Python 3.12+、需自行配置模型服务）、是否签名、遥测默认行为与同意方式、已知限制。

## 7. 生产 collector（与首发解耦）

客户端已就绪，服务端仍缺：真实域名与 HTTPS 证书、保留周期、访问控制、删除接口、禁止持久化原始 IP 的实现与审计记录。未完成前保持 `DOCUAGENT_TELEMETRY_UPLOAD_URL` 未配置，客户端不会产生任何上传请求。

## 8. 已知边界（发布说明需如实写明）

- 桌面版依赖系统 Python 3.12+；打包未内置运行时。
- 安装包未签名、无自动更新链。
- 生产 collector 未部署；本地统计默认开启，上传默认关闭。
- 免费范围为新项目交付与整体开源检索；已有项目重建、逐模块外部组件选型、私有团队扩展不在本仓库。
