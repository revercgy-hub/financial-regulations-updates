# 知识库更新部署

刷新财政部、证监会和中央纪委国家监委的当前案例；审计署保留已经核验的专项案例历史全集：

```powershell
python .\deployment\refresh_cases.py --ccdi-pages 2 --ccdi-detail-limit 40
```

也可以使用 `--sources mof,csrc` 或 `--sources ccdi` 单独刷新来源。脚本会检查各来源数量，官网异常返回不完整数据时停止替换。

生成包含金融监管制度库、会计制度库和四来源案例库的 APP 联网更新包：

```powershell
python .\deployment\build_regulations_package.py --version 20260805.8
```

输出文件位于 `deployment/dist/`：

- `knowledge-package-<version>.zip`：APP 下载并安装的知识库包。
- `latest.json`：复制到 `deployment/update/latest.json` 并提交到 GitHub 的版本清单。

APP 固定从以下地址读取清单：

`https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json`

APP 启动时检查该清单，Android 后台任务也会在联网时每天检查一次。金融监管制度、会计制度和案例位于同一个带版本号的更新包中，经大小和 SHA-256 校验后一起切换，避免各库版本不一致。
