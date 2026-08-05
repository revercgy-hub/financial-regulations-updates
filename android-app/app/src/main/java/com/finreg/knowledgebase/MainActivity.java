package com.finreg.knowledgebase;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.print.PrintAttributes;
import android.print.PrintManager;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.MimeTypeMap;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedCallback;

import org.json.JSONArray;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final String AUTHORITY = "com.finreg.knowledgebase.exports";
    private static final int TEAL = Color.rgb(0, 105, 92);
    private static final int LIBRARY_REGULATIONS = 0;
    private static final int LIBRARY_ACCOUNTING = 1;
    private static final int LIBRARY_CASES = 2;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private WebView webView;
    private ProgressBar progress;
    private TextView title;
    private RegulationUpdater updater;
    private AlertDialog updateDialog;
    private TextView updateMessage;
    private ProgressBar updateProgress;
    private Object backCallback;
    private boolean clearHistoryAfterHomeLoad;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        updater = new RegulationUpdater(this, io);
        KnowledgeUpdateScheduler.schedule(this);
        buildLayout();
        configureWebView();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            backCallback = Api33Back.register(this);
        }
        if (updater.hasCurrentPackage()) {
            if (state == null) loadSearchHome(false); else webView.restoreState(state);
            checkForUpdates(false);
        } else {
            showWaitingPage();
            checkForUpdates(false);
        }
    }

    @Override protected void onStart() {
        super.onStart();
        KnowledgeUpdateScheduler.setAppForeground(true);
    }

    @Override protected void onStop() {
        KnowledgeUpdateScheduler.setAppForeground(false);
        super.onStop();
    }

    private void buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(6), 0, dp(4), 0);
        bar.setBackgroundColor(TEAL);
        bar.setMinimumHeight(dp(56));

        ImageButton back = actionButton(R.drawable.ic_back, "返回上一页");
        back.setOnClickListener(v -> navigateBack());
        bar.addView(back);

        ImageButton home = actionButton(android.R.drawable.ic_menu_search, "返回当前检索首页");
        home.setOnClickListener(v -> returnToSearchHome());
        bar.addView(home);

        title = new TextView(this);
        title.setText("金融与会计知识库");
        title.setTextColor(Color.WHITE);
        title.setTextSize(18);
        title.setMaxLines(1);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        title.setPadding(dp(8), 0, dp(4), 0);
        bar.addView(title, new LinearLayout.LayoutParams(0, dp(56), 1));

        ImageButton share = actionButton(android.R.drawable.ic_menu_share, "分享当前内容");
        share.setOnClickListener(v -> shareCurrentText());
        bar.addView(share);

        ImageButton more = actionButton(android.R.drawable.ic_menu_more, "导出与更多操作");
        more.setOnClickListener(this::showActions);
        bar.addView(more);
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)));

        // Android 15 强制边到边布局；让状态栏沿用顶部绿色，同时避免按钮和内容被系统栏遮挡。
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                left = bars.left; top = bars.top; right = bars.right; bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, 0, right, bottom);
            bar.setPadding(dp(6), top, dp(4), 0);
            ViewGroup.LayoutParams params = bar.getLayoutParams();
            params.height = dp(56) + top;
            bar.setLayoutParams(params);
            return insets;
        });

        FrameLayout content = new FrameLayout(this);
        webView = new WebView(this);
        content.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(-1, dp(3), Gravity.TOP);
        content.addView(progress, pp);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        root.requestApplyInsets();
    }

    private ImageButton actionButton(int icon, String description) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(icon);
        button.setColorFilter(Color.WHITE);
        button.setContentDescription(description);
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setPadding(dp(13), dp(13), dp(13), dp(13));
        button.setLayoutParams(new LinearLayout.LayoutParams(dp(52), dp(52)));
        return button;
    }

    @SuppressLint("SetJavaScriptEnabled") // 已校验的本地制度索引依赖 JS；远程正文链接交给外部浏览器。
    private void configureWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(false);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setDefaultTextEncodingName("UTF-8");
        webView.setWebViewClient(new LocalClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value >= 100 ? View.GONE : View.VISIBLE);
            }
            @Override public void onReceivedTitle(WebView view, String value) {
                if (value != null && !value.startsWith("file:")) title.setText(value);
            }
        });
        webView.setDownloadListener((url, userAgent, disposition, mime, length) -> openLink(Uri.parse(url)));
    }

    private final class LocalClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return openLink(request.getUrl());
        }
        @SuppressWarnings("deprecation")
        @Override public boolean shouldOverrideUrlLoading(WebView view, String url) {
            return openLink(Uri.parse(url));
        }
        @Override public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            if (clearHistoryAfterHomeLoad && isHomeUrl(url)) {
                clearHistoryAfterHomeLoad = false;
                view.clearHistory();
            }
        }
    }

    /** @return true 表示已经由原生层接管。 */
    private boolean openLink(Uri uri) {
        String scheme = uri.getScheme();
        if ("file".equalsIgnoreCase(scheme)) {
            String path = Uri.decode(uri.getPath());
            if (isLegacySearchPath(path)) {
                redirectLegacySearch(uri);
                return true;
            }
            if (path == null) return true;
            File local = new File(path);
            if (!updater.containsCurrentFile(local)) {
                Toast.makeText(this, "已阻止知识库之外的本地文件", Toast.LENGTH_SHORT).show();
                return true;
            }
            String lower = path.toLowerCase(Locale.ROOT);
            if (lower.endsWith(".html") || lower.endsWith(".htm") || !hasExtension(path)) return false;
            if (lower.endsWith(".md")) openMarkdownFile(local); else extractAndOpen(local);
            return true;
        }
        if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme) ||
                "mailto".equalsIgnoreCase(scheme) || "tel".equalsIgnoreCase(scheme)) {
            try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
            catch (Exception e) { Toast.makeText(this, "没有可打开此链接的应用", Toast.LENGTH_SHORT).show(); }
            return true;
        }
        return false;
    }

    private boolean isLegacySearchPath(String path) {
        return path != null && path.endsWith("/打开知识库.html");
    }

    private void redirectLegacySearch(Uri uri) {
        String query = uri.getEncodedQuery();
        String destination = buildHomeUrl(currentLibrary(), "_home");
        if (query != null && !query.isEmpty()) destination += "&" + query;
        clearHistoryAfterHomeLoad = true;
        webView.stopLoading();
        webView.loadUrl(destination);
    }

    private boolean hasExtension(String path) {
        int slash = path.lastIndexOf('/');
        int dot = path.lastIndexOf('.');
        return dot > slash;
    }

    private void showActions(View anchor) {
        File markdown = currentMarkdownFile();
        ArrayList<String> actions = new ArrayList<>();
        if (markdown != null) actions.add("打开 Markdown 原文");
        actions.add("分享所选文字/当前条文");
        actions.add("导出条文为 TXT 文件");
        actions.add("打印或保存为 PDF");
        actions.add("返回当前检索首页");
        int currentLibrary = currentLibrary();
        if (currentLibrary != LIBRARY_REGULATIONS) actions.add("打开金融监管制度库");
        if (currentLibrary != LIBRARY_ACCOUNTING) actions.add("打开会计制度库");
        if (currentLibrary != LIBRARY_CASES) actions.add("打开案例库");
        actions.add("立即检查知识库更新");
        if (updater.hasBackup()) actions.add("恢复上一版知识库");
        actions.add("关于联网同步版");
        String[] items = actions.toArray(new String[0]);
        new AlertDialog.Builder(this).setTitle("导出与更多").setItems(items, (d, which) -> {
            String selected = items[which];
            if ("打开 Markdown 原文".equals(selected)) openMarkdownFile(markdown);
            else if ("分享所选文字/当前条文".equals(selected)) shareCurrentText();
            else if ("导出条文为 TXT 文件".equals(selected)) exportCurrentText();
            else if ("打印或保存为 PDF".equals(selected)) printCurrentPage();
            else if ("返回当前检索首页".equals(selected)) returnToSearchHome();
            else if ("打开金融监管制度库".equals(selected)) openLibraryHome(LIBRARY_REGULATIONS);
            else if ("打开会计制度库".equals(selected)) openLibraryHome(LIBRARY_ACCOUNTING);
            else if ("打开案例库".equals(selected)) openLibraryHome(LIBRARY_CASES);
            else if ("立即检查知识库更新".equals(selected)) checkForUpdates(true);
            else if ("恢复上一版知识库".equals(selected)) confirmRollback();
            else showAbout();
        }).show();
    }

    private File currentMarkdownFile() {
        if (webView == null) return null;
        String url = webView.getUrl();
        if (url == null) return null;
        Uri uri = Uri.parse(url);
        if (!"file".equalsIgnoreCase(uri.getScheme())) return null;
        String path = Uri.decode(uri.getPath());
        if (path == null) return null;
        File html = new File(path);
        if (!updater.containsCurrentFile(html)) return null;
        String packageRoot = updater.getCurrentRoot().getAbsolutePath() + File.separator;
        String regulationRoot = packageRoot + "docs" + File.separator;
        String caseRoot = packageRoot + "cases" + File.separator;
        String relative;
        File markdownRoot;
        if (html.getAbsolutePath().startsWith(regulationRoot)) {
            relative = html.getAbsolutePath().substring(regulationRoot.length());
            markdownRoot = new File(updater.getCurrentRoot(), "data/markdown");
        } else if (html.getAbsolutePath().startsWith(caseRoot)) {
            relative = html.getAbsolutePath().substring(caseRoot.length());
            markdownRoot = new File(updater.getCurrentRoot(), "case-data/markdown");
        } else {
            return null;
        }
        String lower = relative.toLowerCase(Locale.ROOT);
        if (lower.endsWith(".html")) relative = relative.substring(0, relative.length() - 5);
        else if (lower.endsWith(".htm")) relative = relative.substring(0, relative.length() - 4);
        else return null;
        File markdown = new File(markdownRoot, relative + ".md");
        return updater.containsCurrentFile(markdown) ? markdown : null;
    }

    private void shareCurrentText() {
        readPageText((pageTitle, text) -> {
            String body = text.length() > 100_000 ? text.substring(0, 100_000) + "\n\n（内容较长，已截取；可使用“导出条文”分享完整文件）" : text;
            Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain")
                    .putExtra(Intent.EXTRA_SUBJECT, pageTitle)
                    .putExtra(Intent.EXTRA_TEXT, pageTitle + "\n\n" + body + "\n\n— 来自金融监管知识库联网同步版");
            startActivity(Intent.createChooser(send, "分享条文"));
        });
    }

    private void exportCurrentText() {
        readPageText((pageTitle, text) -> io.execute(() -> {
            try {
                File dir = new File(getCacheDir(), "exports");
                if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("无法建立导出目录");
                String filename = safeName(pageTitle) + "_" + stamp() + ".txt";
                File file = new File(dir, filename);
                String content = pageTitle + "\n\n" + text + "\n\n— 导出自金融监管知识库联网同步版\n";
                try (FileOutputStream out = new FileOutputStream(file)) {
                    out.write(content.getBytes(StandardCharsets.UTF_8));
                }
                runOnUiThread(() -> shareFile(file, "text/plain", "导出并分享条文"));
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "导出失败：" + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }));
    }

    private void printCurrentPage() {
        PrintManager manager = (PrintManager) getSystemService(PRINT_SERVICE);
        String job = safeName(String.valueOf(title.getText()));
        manager.print(job, webView.createPrintDocumentAdapter(job),
                new PrintAttributes.Builder().setMediaSize(PrintAttributes.MediaSize.ISO_A4).build());
    }

    private interface PageTextCallback { void ready(String title, String text); }

    private void readPageText(PageTextCallback callback) {
        String script = "(function(){var s=String(window.getSelection&&window.getSelection()).trim();" +
                "return (document.title||'金融监管资料')+'\\u0000'+(s||document.body.innerText||'');})()";
        webView.evaluateJavascript(script, value -> {
            try {
                String decoded = new JSONArray("[" + value + "]").getString(0);
                int split = decoded.indexOf('\0');
                String pageTitle = split >= 0 ? decoded.substring(0, split) : String.valueOf(title.getText());
                String text = split >= 0 ? decoded.substring(split + 1) : decoded;
                if (text.trim().isEmpty()) throw new IllegalStateException("当前页面没有可导出的文字");
                callback.ready(pageTitle.trim(), text.trim());
            } catch (Exception e) {
                Toast.makeText(this, "读取页面内容失败，请等待页面加载完成", Toast.LENGTH_LONG).show();
            }
        });
    }

    private void extractAndOpen(File source) {
        Toast.makeText(this, "正在准备附件…", Toast.LENGTH_SHORT).show();
        io.execute(() -> {
            try {
                if (!updater.containsCurrentFile(source)) throw new IllegalStateException("附件路径无效");
                File dir = new File(getCacheDir(), "exports");
                if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("无法建立附件目录");
                String original = source.getName();
                File file = new File(dir, safeNameWithExtension(original));
                try (InputStream in = new FileInputStream(source);
                     FileOutputStream out = new FileOutputStream(file)) {
                    byte[] buffer = new byte[64 * 1024];
                    int n;
                    while ((n = in.read(buffer)) >= 0) out.write(buffer, 0, n);
                }
                runOnUiThread(() -> {
                    String mime = mimeFor(file.getName());
                    Uri content = contentUri(file);
                    Intent view = new Intent(Intent.ACTION_VIEW).setDataAndType(content, mime)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    view.setClipData(ClipData.newRawUri(file.getName(), content));
                    try { startActivity(Intent.createChooser(view, "打开附件")); }
                    catch (Exception e) { shareFile(file, mime, "分享附件"); }
                });
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "附件打开失败：" + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        });
    }

    private void openMarkdownFile(File markdownFile) {
        if (markdownFile == null) return;
        Toast.makeText(this, "正在打开 Markdown 原文…", Toast.LENGTH_SHORT).show();
        io.execute(() -> {
            try (InputStream in = new FileInputStream(markdownFile);
                  ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                if (!updater.containsCurrentFile(markdownFile)) {
                    throw new IllegalStateException("Markdown 路径无效");
                }
                byte[] buffer = new byte[64 * 1024];
                int n;
                while ((n = in.read(buffer)) >= 0) out.write(buffer, 0, n);
                String markdown = out.toString(StandardCharsets.UTF_8.name());
                String filename = markdownFile.getName();
                String documentTitle = filename;
                for (String line : markdown.split("\\R", 40)) {
                    if (line.startsWith("# ") && line.length() > 2) {
                        documentTitle = line.substring(2).trim();
                        break;
                    }
                }
                String html = "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">" +
                        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
                        "<title>Markdown 原文 · " + htmlEscape(documentTitle) + "</title>" +
                        "<style>body{margin:0;background:#f3f6f5;color:#172422;font-family:sans-serif}" +
                        "header{position:sticky;top:0;padding:14px 18px;background:#e0f2f1;color:#075e54;" +
                        "font-weight:700;border-bottom:1px solid #b2dfdb}" +
                        "pre{box-sizing:border-box;max-width:980px;margin:0 auto;padding:22px 18px 60px;" +
                        "white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.75 sans-serif}</style></head>" +
                        "<body><header>Markdown 原文 · " + htmlEscape(filename) + "</header><pre>" +
                        htmlEscape(markdown) + "</pre></body></html>";
                String baseUrl = Uri.fromFile(markdownFile).toString();
                runOnUiThread(() -> webView.loadDataWithBaseURL(
                        baseUrl, html, "text/html", "UTF-8", baseUrl));
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this,
                        "Markdown 打开失败：" + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        });
    }

    private String htmlEscape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;");
    }

    private void shareFile(File file, String mime, String chooserTitle) {
        Uri uri = contentUri(file);
        Intent send = new Intent(Intent.ACTION_SEND).setType(mime)
                .putExtra(Intent.EXTRA_STREAM, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        send.setClipData(ClipData.newRawUri(file.getName(), uri));
        startActivity(Intent.createChooser(send, chooserTitle));
    }

    private Uri contentUri(File file) {
        return new Uri.Builder().scheme("content").authority(AUTHORITY).appendPath(file.getName()).build();
    }

    private String mimeFor(String name) {
        String ext = MimeTypeMap.getFileExtensionFromUrl(name.toLowerCase(Locale.ROOT));
        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        return mime == null ? "application/octet-stream" : mime;
    }

    private String safeName(String value) {
        String cleaned = value.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").trim();
        if (cleaned.isEmpty()) cleaned = "金融监管资料";
        return cleaned.length() > 60 ? cleaned.substring(0, 60) : cleaned;
    }

    private String safeNameWithExtension(String value) {
        String cleaned = value.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").trim();
        if (cleaned.isEmpty()) cleaned = "附件";
        return cleaned.length() > 100 ? cleaned.substring(cleaned.length() - 100) : cleaned;
    }

    private String stamp() {
        return new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).format(new Date());
    }

    private void checkForUpdates(boolean manual) {
        updater.check(manual, new RegulationUpdater.Listener() {
            @Override public void onChecking(boolean required) {
                runOnUiThread(() -> {
                    if (required || manual) showUpdateDialog("正在检查知识库版本…", 0, true);
                });
            }

            @Override public void onDownloadStarted(String version, long bytes, boolean required) {
                runOnUiThread(() -> showUpdateDialog(
                        "发现知识库版本 " + version + "，正在下载 " + formatSize(bytes) + "…", 0, true));
            }

            @Override public void onProgress(String message, int percent, boolean required) {
                runOnUiThread(() -> showUpdateDialog(message + " " + percent + "%", percent, true));
            }

            @Override public void onReady(boolean installed, String version, int documents) {
                runOnUiThread(() -> {
                    dismissUpdateDialog();
                    loadSearchHome(true);
                    Toast.makeText(MainActivity.this,
                            "知识库已启用 " + version + "（金融制度 " + updater.getRegulationDocuments() +
                                    " 篇，会计制度 " + updater.getAccountingDocuments() +
                                    " 篇，案例 " + updater.getCaseDocuments() + " 条）",
                            Toast.LENGTH_LONG).show();
                });
            }

            @Override public void onNoUpdate(String version, int documents, boolean wasManual) {
                runOnUiThread(() -> {
                    dismissUpdateDialog();
                    if (wasManual) Toast.makeText(MainActivity.this,
                            "已是最新知识库版本 " + version + "（金融制度 " + updater.getRegulationDocuments() +
                                    " 篇，会计制度 " + updater.getAccountingDocuments() +
                                    " 篇，案例 " + updater.getCaseDocuments() + " 条）",
                            Toast.LENGTH_LONG).show();
                });
            }

            @Override public void onError(String message, boolean hasUsablePackage, boolean wasManual) {
                runOnUiThread(() -> {
                    dismissUpdateDialog();
                    if (hasUsablePackage) {
                        if (wasManual) Toast.makeText(MainActivity.this,
                                "检查更新失败，继续使用现有知识库：" + message,
                                Toast.LENGTH_LONG).show();
                        return;
                    }
                    showWaitingPage();
                    new AlertDialog.Builder(MainActivity.this)
                            .setTitle("知识库同步失败")
                            .setMessage(message + "\n\n首次使用必须联网下载知识库，请检查网络后重试。")
                            .setCancelable(false)
                            .setPositiveButton("重试", (dialog, which) -> checkForUpdates(true))
                            .setNegativeButton("退出", (dialog, which) -> finish())
                            .show();
                });
            }
        });
    }

    private void confirmRollback() {
        new AlertDialog.Builder(this)
                .setTitle("恢复上一版知识库")
                .setMessage("当前版本会保留为备份，可再次切换回来。是否继续？")
                .setNegativeButton("取消", null)
                .setPositiveButton("恢复", (dialog, which) -> {
                    showUpdateDialog("正在恢复上一版本…", 0, true);
                    updater.rollback(new RegulationUpdater.Listener() {
                        @Override public void onChecking(boolean required) {}
                        @Override public void onDownloadStarted(String version, long bytes, boolean required) {}
                        @Override public void onProgress(String message, int percent, boolean required) {}
                        @Override public void onNoUpdate(String version, int documents, boolean manual) {}
                        @Override public void onReady(boolean installed, String version, int documents) {
                            runOnUiThread(() -> {
                                dismissUpdateDialog();
                                loadSearchHome(true);
                                Toast.makeText(MainActivity.this,
                                        "已恢复知识库版本 " + version, Toast.LENGTH_LONG).show();
                            });
                        }
                        @Override public void onError(String message, boolean usable, boolean manual) {
                            runOnUiThread(() -> {
                                dismissUpdateDialog();
                                Toast.makeText(MainActivity.this,
                                        "恢复失败：" + message, Toast.LENGTH_LONG).show();
                            });
                        }
                    });
                }).show();
    }

    private void showUpdateDialog(String message, int percent, boolean indeterminateWhenZero) {
        if (updateDialog == null) {
            LinearLayout layout = new LinearLayout(this);
            layout.setOrientation(LinearLayout.VERTICAL);
            layout.setPadding(dp(24), dp(12), dp(24), dp(8));
            updateMessage = new TextView(this);
            updateMessage.setTextSize(16);
            updateMessage.setTextColor(Color.rgb(35, 48, 46));
            updateProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            updateProgress.setMax(100);
            layout.addView(updateMessage, new LinearLayout.LayoutParams(-1, -2));
            LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(-1, dp(6));
            progressParams.topMargin = dp(18);
            layout.addView(updateProgress, progressParams);
            updateDialog = new AlertDialog.Builder(this)
                    .setTitle("联网同步金融监管制度")
                    .setView(layout)
                    .setCancelable(false)
                    .create();
            updateDialog.show();
        }
        updateMessage.setText(message);
        updateProgress.setIndeterminate(indeterminateWhenZero && percent <= 0);
        if (percent > 0) updateProgress.setProgress(percent);
    }

    private void dismissUpdateDialog() {
        if (updateDialog != null) {
            updateDialog.dismiss();
            updateDialog = null;
            updateMessage = null;
            updateProgress = null;
        }
    }

    private void showWaitingPage() {
        String html = "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">" +
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>" +
                "<body style=\"font-family:sans-serif;background:#f3f6f5;color:#173a35;padding:48px 24px\">" +
                "<h2>正在准备金融与会计知识库</h2><p>首次使用需要联网下载金融制度、会计制度与案例数据；以后会自动检查更新。</p>" +
                "</body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }

    private String formatSize(long bytes) {
        return String.format(Locale.ROOT, "%.1f MB", bytes / 1024.0 / 1024.0);
    }

    private void showAbout() {
        new AlertDialog.Builder(this).setTitle("金融与会计知识库 · 联网同步版")
                .setMessage("APP 版本：" + BuildConfig.VERSION_NAME + "\n" +
                        "制度版本：" + updater.getVersion() + "\n" +
                        "金融监管制度：" + updater.getRegulationDocuments() + " 篇\n" +
                        "会计制度：" + updater.getAccountingDocuments() + " 篇\n\n" +
                        "案例数量：" + updater.getCaseDocuments() + " 条\n" +
                        "案例来源：财政部、证监会、审计署、中央纪委国家监委\n\n" +
                        "启动时检查更新，并由 Android 在联网时每天后台检查一次。" +
                        "金融制度、会计制度和案例库始终作为同一版本更新。下载包会校验文件大小和 SHA-256，" +
                        "安装失败不会覆盖现有数据，并保留上一版本用于回滚。\n\n" +
                        "更新源：GitHub · " + RegulationUpdater.MANIFEST_URL)
                .setPositiveButton("确定", null).show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private boolean isHomeUrl(String url) {
        String regulationHome = libraryHomeUrl(LIBRARY_REGULATIONS);
        String caseHome = libraryHomeUrl(LIBRARY_CASES);
        return url == null || matchesHome(url, regulationHome) || matchesHome(url, caseHome);
    }

    private boolean matchesHome(String url, String home) {
        return url.equals(home) || url.startsWith(home + "?") || url.startsWith(home + "#");
    }

    private boolean isCaseView() {
        String url = webView == null ? null : webView.getUrl();
        if (url == null) return false;
        Uri uri = Uri.parse(url);
        if (!"file".equalsIgnoreCase(uri.getScheme())) return false;
        String path = Uri.decode(uri.getPath());
        if (path == null) return false;
        String root = updater.getCurrentRoot().getAbsolutePath() + File.separator;
        return path.startsWith(root + "cases" + File.separator)
                || path.startsWith(root + "case-data" + File.separator);
    }

    private boolean isAccountingView() {
        String url = webView == null ? null : webView.getUrl();
        if (url == null) return false;
        Uri uri = Uri.parse(url);
        if (!"file".equalsIgnoreCase(uri.getScheme())) return false;
        if ("accounting".equals(uri.getQueryParameter("collection"))) return true;
        String path = Uri.decode(uri.getPath());
        if (path == null) return false;
        String root = updater.getCurrentRoot().getAbsolutePath() + File.separator;
        return path.startsWith(root + "docs" + File.separator + "accounting" + File.separator);
    }

    private int currentLibrary() {
        if (isCaseView()) return LIBRARY_CASES;
        if (isAccountingView()) return LIBRARY_ACCOUNTING;
        return LIBRARY_REGULATIONS;
    }

    private String libraryHomeUrl(int library) {
        File home = library == LIBRARY_CASES
                ? new File(updater.getCurrentRoot(), "cases/index.html")
                : updater.getHomeFile();
        return Uri.fromFile(home).toString();
    }

    private String buildHomeUrl(int library, String marker) {
        String url = libraryHomeUrl(library) + "?" + marker + "=" + System.currentTimeMillis();
        if (library == LIBRARY_REGULATIONS) return url + "&collection=regulations";
        if (library == LIBRARY_ACCOUNTING) return url + "&collection=accounting";
        return url;
    }

    private void openLibraryHome(int library) {
        File home = library == LIBRARY_CASES
                ? new File(updater.getCurrentRoot(), "cases/index.html")
                : updater.getHomeFile();
        if (!home.isFile()) {
            String name = library == LIBRARY_CASES ? "案例库" : "制度库";
            Toast.makeText(this, "当前数据包尚未包含" + name + "，请立即检查更新", Toast.LENGTH_LONG).show();
            checkForUpdates(true);
            return;
        }
        if (library == LIBRARY_ACCOUNTING && updater.getAccountingDocuments() <= 0) {
            Toast.makeText(this, "当前版本尚未包含会计制度，请立即检查更新", Toast.LENGTH_LONG).show();
            checkForUpdates(true);
            return;
        }
        clearHistoryAfterHomeLoad = true;
        webView.stopLoading();
        webView.loadUrl(buildHomeUrl(library, "_home"));
    }

    private void loadSearchHome(boolean clearSearch) {
        if (webView == null) return;
        clearHistoryAfterHomeLoad = true;
        webView.stopLoading();
        String marker = clearSearch ? "_home" : "_restore";
        if (!updater.hasCurrentPackage()) {
            showWaitingPage();
            return;
        }
        webView.loadUrl(buildHomeUrl(currentLibrary(), marker));
    }

    private void returnToSearchHome() {
        if (webView == null) return;
        if (!isHomeUrl(webView.getUrl())) {
            loadSearchHome(true);
            return;
        }
        String collection = currentLibrary() == LIBRARY_ACCOUNTING
                ? "accounting" : currentLibrary() == LIBRARY_REGULATIONS ? "regulations" : "";
        String script = "(function(){if(typeof window.KB_RESET_SEARCH==='function'){" +
                "window.KB_RESET_SEARCH('" + collection + "');return true;}var c=document.getElementById('clearFilter');" +
                "if(c){c.click();return true;}return false;})()";
        webView.evaluateJavascript(script, value -> {
            if (!"true".equals(value)) loadSearchHome(true);
        });
    }

    private void resetSearchOrToast() {
        String script = "(function(){if(typeof window.KB_HAS_ACTIVE_SEARCH==='function'&&" +
                "window.KB_HAS_ACTIVE_SEARCH()){window.KB_RESET_SEARCH();return true;}" +
                "var c=document.getElementById('clearFilter');if(c){c.click();return true;}" +
                "return false;})()";
        webView.evaluateJavascript(script, value -> {
            if (!"true".equals(value)) {
                Toast.makeText(this, "已在检索首页", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void navigateBack() {
        if (webView == null) return;
        String url = webView.getUrl();
        if (isHomeUrl(url)) {
            resetSearchOrToast();
        } else if (isMarkdownUrl(url) && webView.canGoBack()) {
            webView.goBack();
        } else {
            loadSearchHome(false);
        }
    }

    private boolean isMarkdownUrl(String url) {
        if (url == null) return false;
        String path = Uri.parse(url).getPath();
        return path != null && path.toLowerCase(Locale.ROOT).endsWith(".md");
    }

    private void handleSystemBack() {
        if (webView == null) {
            finish();
            return;
        }
        if (!isHomeUrl(webView.getUrl())) {
            if (isMarkdownUrl(webView.getUrl()) && webView.canGoBack()) webView.goBack();
            else loadSearchHome(false);
            return;
        }
        String script = "(function(){if(typeof window.KB_HAS_ACTIVE_SEARCH==='function'&&" +
                "window.KB_HAS_ACTIVE_SEARCH()){window.KB_RESET_SEARCH();return true;}" +
                "return false;})()";
        webView.evaluateJavascript(script, value -> {
            if (!"true".equals(value)) finish();
        });
    }

    @Override public void onBackPressed() {
        handleSystemBack();
    }

    @Override protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override protected void onDestroy() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && backCallback != null) {
            Api33Back.unregister(this, backCallback);
        }
        dismissUpdateDialog();
        io.shutdownNow();
        if (webView != null) webView.destroy();
        super.onDestroy();
    }

    /** 隔离 Android 13 类型，避免旧版 Android 在加载 Activity 时解析不存在的系统类。 */
    @android.annotation.TargetApi(Build.VERSION_CODES.TIRAMISU)
    private static final class Api33Back {
        private static Object register(MainActivity activity) {
            OnBackInvokedCallback callback = activity::handleSystemBack;
            activity.getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, callback);
            return callback;
        }

        private static void unregister(MainActivity activity, Object callback) {
            activity.getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(
                    (OnBackInvokedCallback) callback);
        }
    }
}
