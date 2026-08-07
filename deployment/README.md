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

## GitHub 服务器自动更新

`.github/workflows/automatic-knowledge-update.yml` 使用上一版 Release 中的 `knowledge-source-state-<version>.zip` 恢复经过核验的源数据，然后执行：

- 每天北京时间 04:20：财政部、证监会、中央纪委国家监委案例增量检查，以及财政部会计司官方政策、通知和解读增量检查。
- 每周日北京时间 04:20：在每日增量检查基础上，追加金融制度、MaoDocs 会计资料、证监会目录和审计署完整检查。
- 手动触发：可选择仅检查财政部会计司的 `accounting`、案例增量 `cases` 或全库 `all`，并支持只构建不发布的 `dry_run`。

自动任务先比较三套源库的规范化内容指纹。没有变化时不生成 Release；发现变化时先执行来源数量下限、四来源集合、ZIP 路径、文件数量和 SHA-256 校验，再创建 Release，最后才提交 `deployment/update/latest.json`。任一步失败都不会覆盖手机当前使用的版本。

工作流需要仓库 Actions 的 `GITHUB_TOKEN` 具备 `contents: write` 权限；不需要额外的个人令牌或服务器密码。
