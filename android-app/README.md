# 金融监管知识库 Android 联网同步版

原生 Android 查询 APP。APK 不内置大体积数据，首次启动时从 GitHub Release 下载制度和案例知识库，后续自动检查版本。

## 功能

- 联网同步 2,021 篇金融监管制度。
- 联网同步 2,810 条案例，来源包括财政部、证监会、审计署、中央纪委国家监委。
- APP 内切换制度库和案例库；案例支持机构、主体、处理类型、案由和通告阶段筛选。
- 下载文件大小和 SHA-256 双重校验。
- 临时目录解压、原子替换和上一版本回滚。
- 查看 Markdown 原文，支持分享、导出 TXT、打印或保存 PDF。

## 构建

```powershell
.\build-apk.ps1
```

输出：`FinReg-KnowledgeBase-Online-v1.7.1.apk`

首次构建会把 JDK 17、Android SDK Platform 35、Build Tools 35.0.0 和 Gradle 8.9 下载到 `%LOCALAPPDATA%\FinRegAndroidBuild`。

## 更新清单

APP 固定读取：

`https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json`

清单指定知识库版本、制度和案例数量、四类案例来源统计、下载地址、文件大小、SHA-256 和最低 APP 版本。
