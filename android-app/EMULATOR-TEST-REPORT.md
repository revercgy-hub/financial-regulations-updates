# 金融监管制度库 Android 模拟器测试报告

测试日期：2026-08-05

## 测试环境

- AVD：`FinReg_API35`
- Android：15 / API 35 / Google APIs x86_64
- APK：`FinReg-KnowledgeBase-Online-v1.6.0.apk`
- 包名：`com.finreg.knowledgebase`
- APP 版本：versionCode 8 / versionName 1.6.0
- 制度版本：`20260805.1`
- 更新来源：GitHub Release 公网地址

## 联网同步验证

- 卸载旧版并清空应用数据后首次启动，APP 自动读取公开 `latest.json`。
- 真实下载 28,627,515 字节制度包，没有使用 ADB 预置数据。
- 下载进度从 0% 持续更新到 100%。
- 文件大小和 SHA-256 校验通过。
- 安全解压、临时目录安装和目录切换成功。
- 内部制度目录包含：
  - 2,021 个 HTML 正文
  - 2,021 个 Markdown 原文
  - 16 个全文检索分片
- 手动“立即检查制度更新”识别为最新版本，没有重复下载制度包。
- 强制停止后冷启动直接载入已同步内容，后台检查更新不阻塞首页。

## 功能回归

- 首页显示 2,021 篇金融监管制度。
- 输入 `basel`，全文检索返回 5 篇匹配文档。
- 检索结果正文正常打开。
- Android 系统返回键回到制度检索页，并保留 `basel` 和 5 篇结果。
- 顶部制度检索首页按钮清空关键词，恢复显示 2,021 篇文档。
- Markdown 原文可从原生“导出与更多”菜单打开。
- 测试期间没有应用 Fatal Exception、ANR 或 OutOfMemory。

## 构建与安全检查

- Gradle `assembleDebug`：通过。
- Android Lint：0 个错误、1 个 targetSdk 兼容性提示。
- APK Signature Scheme v2：验证通过。
- APK 大小：41,280 字节；旧 v1.5.0 为 96,840,428 字节。
- APK SHA-256：`e32b59db65ae81fcbc0be7702ecbb25aa91726e3fdab1ff89f2348e844ea87e8`。
- 制度包大小：28,627,515 字节。
- 制度包 SHA-256：`987f67adb73efd9d5b08d2185ee1ba3cd909babdab55a1b38d0e009c415309fe`。

## 结论

v1.6.0 已实现“小 APK + GitHub 联网同步制度库”。首次同步、校验、安装、检索、正文、返回、Markdown、手动更新检查和冷启动缓存均通过模拟器验证。上一版本回滚入口会在成功安装第二个制度版本后自动出现。
