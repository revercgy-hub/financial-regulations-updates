# 金融、会计与案例知识库 Android 版

原生 Android 查询 APP。APK 不内置大体积数据，首次启动时从 GitHub Release 下载金融监管制度、会计制度和案例知识库，后续启动时检查更新，并由 Android 在联网时每天执行一次后台检查。

同一工程还可生成完整离线版：金融监管制度、会计制度和四来源案例全部内置在 APK 中，不申请网络权限，文件名包含知识库版本，并可与联网同步版同时安装。

## 功能

- 联网同步 2,021 篇金融监管制度。
- 联网同步 1,291 篇会计制度，覆盖会计、审计、证券、内控、评估。
- 联网同步 2,810 条案例，来源包括财政部、证监会、审计署、中央纪委国家监委。
- APP 内切换金融监管制度库、会计制度库和案例库；案例支持机构、主体、处理类型、案由和通告阶段筛选。
- 下载文件大小和 SHA-256 双重校验。
- 临时目录解压、原子替换和上一版本回滚。
- 金融监管制度、会计制度和案例库使用同一个版本包，自动更新时不会出现各库版本不一致。
- 后台任务仅在 APP 不处于前台查询时切换数据；失败后保留现有版本并由系统重试。
- 查看 Markdown 原文，支持分享、导出 TXT、打印或保存 PDF。

## 构建

```powershell
.\build-apk.ps1
```

输出：`FinReg-KnowledgeBase-Online-v1.7.4.apk`

构建当前清单对应的完整离线版：

```powershell
.\build-apk.ps1 -Edition Offline
```

输出示例：`FinReg-KnowledgeBase-Offline-KB20260805.9-v1.7.4.apk`

首次构建会把 JDK 17、Android SDK Platform 35、Build Tools 35.0.0 和 Gradle 8.9 下载到 `%LOCALAPPDATA%\FinRegAndroidBuild`。

## 更新清单

APP 固定读取：

`https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json`

清单指定知识库版本、制度和案例数量、四类案例来源统计、下载地址、文件大小、SHA-256 和最低 APP 版本。
