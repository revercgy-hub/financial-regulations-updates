# 金融监管制度库 Android 联网同步版

原生 Android 查询 APP。v1.6.0 起不再把约 97MB 的完整知识库塞入 APK，而是在首次启动时从 GitHub Release 下载金融监管制度包，后续自动检查版本。

## 功能

- 联网同步 2,021 篇金融监管制度
- 下载文件大小和 SHA-256 双重校验
- 临时目录解压、原子替换和上一版本回滚
- 标题、正文、文号、机构、状态、年份全文检索
- 查看 Markdown 原文
- 分享、导出 TXT、打印或保存 PDF
- 外部正文来源链接交给系统浏览器打开

## 构建

```powershell
.\build-apk.ps1
```

输出：`FinReg-KnowledgeBase-Online-v1.6.0.apk`

首次构建会把 JDK 17、Android SDK Platform 35、Build Tools 35.0.0 和 Gradle 8.9 下载到 `%LOCALAPPDATA%\FinRegAndroidBuild`。

## 更新清单

APP 固定读取：

`https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json`

清单指定制度包版本、文档数量、下载地址、文件大小、SHA-256 和最低 APP 版本。
