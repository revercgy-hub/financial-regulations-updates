# 金融监管知识库 Android 模拟器测试报告

测试日期：2026-08-04

## 测试环境

- AVD：`FinReg_API35`
- 设备模板：Pixel 3a
- Android：15 / API 35 / Google APIs x86_64
- Android Emulator：36.6.11
- 虚拟化加速：AEHD 2.2（检测通过）
- 模拟器内存：2 GB
- APK：`FinReg-KnowledgeBase-Slim-Full-v1.5.0.apk`
- 包名：`com.finreg.knowledgebase`
- 版本：versionCode 7 / versionName 1.5.0

## 已通过项目

- APK 覆盖安装和冷启动。
- 首页显示及 5,438 篇文档目录载入。
- 分片全文检索：输入 `basel`，返回 5 篇，与离线索引预期一致。
- 打开检索结果正文。
- 正文顶部搜索栏输入 `basel` 后正常返回 5 篇匹配文档，不再进入错误页。
- 顶部返回按钮返回统一检索，并保留关键词、筛选条件、结果数量和滚动位置。
- Android 系统返回键返回统一检索，并保留关键词、筛选条件、结果数量和滚动位置。
- 顶部“返回统一检索首页”按钮清空检索状态并显示全部 5,438 篇文档。
- 正文“导出与更多”菜单可直接打开 Markdown 原文，无需滚动到长文档末尾。
- Markdown 返回正文，再返回统一检索，导航层级及检索状态均正常。
- 分享当前条文，系统分享面板正常打开。
- 导出 TXT，实测生成 384,919 字节文件并进入系统分享面板。
- 打印/保存 PDF：
  - 22 KB 短文档生成 8 页预览。
  - 1.17 MB 超长文档生成 362 页、约 30.6 MB 打印数据，但等待时间较长。
- 横屏布局。
- 强制停止后的冷启动。
- 测试期间未发现 APP 进程的 Fatal Exception、ANR 或 OutOfMemory。
- APK v2 签名验证通过；包内 5,438 篇 HTML、5,438 篇 Markdown 和 32 个检索分片完整。
- APK 内 27,190 个正文首页/搜索链接均已指向 Android 首页，旧错误链接数量为 0。
- Android Lint：0 个错误、1 个兼容性提示。

## 检查结论

v1.5.0 修复了正文顶部搜索栏指向不存在旧首页文件的问题，并将系统返回和统一检索改为原生直接导航，不再依赖可能含错误页的 WebView 历史。正文搜索、系统返回、统一检索及旧链接兼容回归均通过。本轮未发现阻断使用的问题。超长文档生成 PDF 仍需较长等待时间，属于文档页数和系统打印服务的性能限制。

## 便捷脚本

启动可见模拟器、安装目录中最新 APK 并打开 APP：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-test-emulator.ps1
```

仅启动/打开 APP，不重新安装 APK：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-test-emulator.ps1 -SkipInstall
```

关闭模拟器：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-test-emulator.ps1
```

测试截图保存在 `test-output` 目录。
