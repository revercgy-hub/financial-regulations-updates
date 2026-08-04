# 制度更新部署

运行以下命令生成只包含金融监管制度的在线更新包：

```powershell
python .\deployment\build_regulations_package.py --version 20260805.1
```

输出文件位于 `deployment/dist/`：

- `regulations-package-<version>.zip`：APP 下载并安装的制度包。
- `latest.json`：复制到 `deployment/update/latest.json` 并提交到 GitHub 的版本清单。

APP 会从以下固定地址读取清单：

`https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json`
