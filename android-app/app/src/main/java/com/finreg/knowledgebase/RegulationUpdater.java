package com.finreg.knowledgebase;

import android.content.Context;
import android.content.SharedPreferences;
import android.system.Os;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

final class RegulationUpdater {
    static final String MANIFEST_URL =
            "https://raw.githubusercontent.com/revercgy-hub/financial-regulations-updates/main/deployment/update/latest.json";
    private static final String OFFLINE_MANIFEST_ASSET = "offline-manifest.json";
    private static final String OFFLINE_PACKAGE_ASSET = "knowledge-package.zip";
    private static final String PREFS = "regulation_updates";
    private static final String KEY_VERSION = "version";
    private static final String KEY_VERSION_CODE = "version_code";
    private static final String KEY_DOCUMENTS = "documents";
    private static final String KEY_REGULATION_DOCUMENTS = "regulation_documents";
    private static final String KEY_ACCOUNTING_DOCUMENTS = "accounting_documents";
    private static final String KEY_FISCAL_DOCUMENTS = "fiscal_documents";
    private static final String KEY_CASE_DOCUMENTS = "case_documents";
    private static final long MAX_MANIFEST_BYTES = 1024 * 1024;
    private static final long MAX_PACKAGE_BYTES = 512L * 1024L * 1024L;
    private static final long MAX_DELTA_BYTES = 384L * 1024L * 1024L;
    private static final long MAX_EXTRACTED_BYTES = 768L * 1024L * 1024L;
    private static final int MAX_ZIP_ENTRIES = 20_000;
    private static final AtomicBoolean UPDATE_RUNNING = new AtomicBoolean(false);

    interface Listener {
        void onChecking(boolean required);
        void onDownloadStarted(String version, long bytes, boolean required);
        void onProgress(String message, int percent, boolean required);
        void onReady(boolean installed, String version, int documents);
        void onNoUpdate(String version, int documents, boolean manual);
        void onError(String message, boolean hasUsablePackage, boolean manual);
    }

    private final Context context;
    private final ExecutorService executor;
    private final File root;
    private final File current;
    private final File backup;
    private final SharedPreferences preferences;

    RegulationUpdater(Context context, ExecutorService executor) {
        this.context = context.getApplicationContext();
        this.executor = executor;
        this.root = new File(context.getFilesDir(), "regulations");
        this.current = new File(root, "current");
        this.backup = new File(root, "backup");
        this.preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    File getCurrentRoot() {
        return current;
    }

    File getHomeFile() {
        return new File(current, "index.html");
    }

    boolean hasCurrentPackage() {
        return getHomeFile().isFile() && new File(current, "package.json").isFile();
    }

    boolean hasBackup() {
        return new File(backup, "index.html").isFile()
                && new File(backup, "package.json").isFile();
    }

    String getVersion() {
        return preferences.getString(KEY_VERSION, hasCurrentPackage() ? "已安装" : "未安装");
    }

    int getDocuments() {
        return preferences.getInt(KEY_DOCUMENTS, 0);
    }

    int getCaseDocuments() {
        return preferences.getInt(KEY_CASE_DOCUMENTS, 0);
    }

    int getRegulationDocuments() {
        return preferences.getInt(KEY_REGULATION_DOCUMENTS, getDocuments());
    }

    int getAccountingDocuments() {
        return preferences.getInt(KEY_ACCOUNTING_DOCUMENTS, 0);
    }

    int getFiscalDocuments() {
        return preferences.getInt(KEY_FISCAL_DOCUMENTS, 0);
    }

    long getVersionCode() {
        return preferences.getLong(KEY_VERSION_CODE, 0L);
    }

    void check(boolean manual, Listener listener) {
        check(manual, () -> true, listener);
    }

    void check(boolean manual, BooleanSupplier installAllowed, Listener listener) {
        final boolean required = !hasCurrentPackage();
        executor.execute(() -> {
            if (!UPDATE_RUNNING.compareAndSet(false, true)) {
                listener.onError("已有知识库更新任务正在运行", hasCurrentPackage(), manual);
                return;
            }
            try {
                listener.onChecking(required);
                JSONObject manifest = fetchManifest();
                validateManifest(manifest);
                String version = manifest.getString("version");
                long remoteVersionCode = manifest.getLong("version_code");
                int documents = manifest.getInt("documents");
                if (hasCurrentPackage() && remoteVersionCode <= getVersionCode()) {
                    listener.onNoUpdate(getVersion(), getDocuments(), manual);
                    return;
                }
                ensureInstallAllowed(installAllowed);
                if (canUseDelta(manifest)) {
                    try {
                        JSONObject delta = manifest.getJSONObject("delta");
                        listener.onDownloadStarted(version, delta.getLong("size"), required);
                        File archive = downloadDelta(delta, listener, required);
                        try {
                            installDelta(archive, manifest, delta, listener, required, installAllowed);
                        } finally {
                            if (archive.exists() && !archive.delete()) archive.deleteOnExit();
                        }
                    } catch (Exception deltaError) {
                        ensureInstallAllowed(installAllowed);
                        listener.onProgress(
                                "增量更新未完成，正在自动改用完整更新包…", 0, required);
                        listener.onDownloadStarted(
                                version, manifest.getLong("package_size"), required);
                        installFullDownload(manifest, listener, required, installAllowed);
                    }
                } else {
                    listener.onDownloadStarted(version, manifest.getLong("package_size"), required);
                    installFullDownload(manifest, listener, required, installAllowed);
                }
                listener.onReady(true, version, documents);
            } catch (Exception error) {
                listener.onError(readableMessage(error), hasCurrentPackage(), manual);
            } finally {
                UPDATE_RUNNING.set(false);
            }
        });
    }

    void rollback(Listener listener) {
        executor.execute(() -> {
            if (!UPDATE_RUNNING.compareAndSet(false, true)) {
                listener.onError("已有知识库更新任务正在运行", hasCurrentPackage(), true);
                return;
            }
            try {
                if (!hasBackup()) {
                    listener.onError("没有可恢复的上一版本", hasCurrentPackage(), true);
                    return;
                }
                File oldCurrent = new File(root, "rollback-old");
                deleteTree(oldCurrent);
                if (current.exists() && !current.renameTo(oldCurrent)) {
                    throw new IllegalStateException("无法暂存当前版本");
                }
                if (!backup.renameTo(current)) {
                    if (oldCurrent.exists()) oldCurrent.renameTo(current);
                    throw new IllegalStateException("无法恢复上一版本");
                }
                if (oldCurrent.exists() && !oldCurrent.renameTo(backup)) {
                    deleteTree(oldCurrent);
                }
                JSONObject installed = readJson(new File(current, "package.json"), 1024 * 1024);
                saveInstalled(installed);
                listener.onReady(true, installed.getString("version"), installed.getInt("documents"));
            } catch (Exception error) {
                listener.onError(readableMessage(error), hasCurrentPackage(), true);
            } finally {
                UPDATE_RUNNING.set(false);
            }
        });
    }

    boolean containsCurrentFile(File file) {
        try {
            String rootPath = current.getCanonicalPath() + File.separator;
            return file.getCanonicalPath().startsWith(rootPath) && file.isFile();
        } catch (Exception ignored) {
            return false;
        }
    }

    private JSONObject fetchManifest() throws Exception {
        if (BuildConfig.OFFLINE_BUILD) {
            try (InputStream input = context.getAssets().open(OFFLINE_MANIFEST_ASSET);
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                copyLimited(input, output, MAX_MANIFEST_BYTES, null);
                return new JSONObject(output.toString(StandardCharsets.UTF_8.name()));
            }
        }
        Exception lastError = null;
        for (int attempt = 1; attempt <= 4; attempt++) {
            URL url = new URL(MANIFEST_URL + "?t=" + System.currentTimeMillis());
            try (InputStream input = openDownload(url);
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                copyLimited(input, output, MAX_MANIFEST_BYTES, null);
                return new JSONObject(output.toString(StandardCharsets.UTF_8.name()));
            } catch (Exception error) {
                lastError = error;
                if (attempt < 4) Thread.sleep(1000L * attempt);
            }
        }
        throw new IllegalStateException(
                "更新清单在自动重试后仍无法读取：" + readableMessage(lastError), lastError);
    }

    private void validateManifest(JSONObject manifest) throws Exception {
        if (manifest.getInt("schema") != 1) throw new IllegalArgumentException("不支持的更新清单格式");
        if (!"regulations".equals(manifest.getString("scope"))) {
            throw new IllegalArgumentException("更新包不是金融监管制度库");
        }
        if (manifest.getInt("min_app_version_code") > BuildConfig.VERSION_CODE) {
            throw new IllegalStateException("制度包需要更新版本的 APP，请先安装最新 APK");
        }
        long size = manifest.getLong("package_size");
        if (size <= 0 || size > MAX_PACKAGE_BYTES) throw new IllegalArgumentException("更新包大小异常");
        String hash = manifest.getString("sha256").toLowerCase(Locale.ROOT);
        if (!hash.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("更新包校验值异常");
        URL packageUrl = new URL(manifest.getString("package_url"));
        if (!"https".equalsIgnoreCase(packageUrl.getProtocol())) {
            throw new IllegalArgumentException("更新包必须使用 HTTPS");
        }
        JSONObject delta = manifest.optJSONObject("delta");
        if (delta != null) {
            if (delta.getInt("schema") != 1) {
                throw new IllegalArgumentException("不支持的增量更新格式");
            }
            if (delta.getLong("base_version_code") <= 0
                    || delta.getLong("base_version_code") >= manifest.getLong("version_code")) {
                throw new IllegalArgumentException("增量更新版本范围异常");
            }
            long deltaSize = delta.getLong("size");
            if (deltaSize <= 0 || deltaSize > MAX_DELTA_BYTES) {
                throw new IllegalArgumentException("增量更新包大小异常");
            }
            String deltaHash = delta.getString("sha256").toLowerCase(Locale.ROOT);
            if (!deltaHash.matches("[0-9a-f]{64}")) {
                throw new IllegalArgumentException("增量更新包校验值异常");
            }
            URL deltaUrl = new URL(delta.getString("url"));
            if (!"https".equalsIgnoreCase(deltaUrl.getProtocol())) {
                throw new IllegalArgumentException("增量更新包必须使用 HTTPS");
            }
        }
    }

    private boolean canUseDelta(JSONObject manifest) throws Exception {
        if (BuildConfig.OFFLINE_BUILD || !hasCurrentPackage()) return false;
        JSONObject delta = manifest.optJSONObject("delta");
        if (delta == null || delta.getLong("base_version_code") != getVersionCode()) return false;
        JSONObject installed = readJson(new File(current, "package.json"), 1024 * 1024);
        return installed.getLong("version_code") == delta.getLong("base_version_code")
                && installed.getString("version").equals(delta.getString("base_version"));
    }

    private void installFullDownload(
            JSONObject manifest, Listener listener, boolean required,
            BooleanSupplier installAllowed
    ) throws Exception {
        File archive = downloadPackage(manifest, listener, required);
        try {
            installPackage(archive, manifest, listener, required, installAllowed);
        } finally {
            if (archive.exists() && !archive.delete()) archive.deleteOnExit();
        }
    }

    private File downloadPackage(
            JSONObject manifest, Listener listener, boolean required
    ) throws Exception {
        if (BuildConfig.OFFLINE_BUILD) {
            return copyBundledPackage(manifest, listener, required);
        }
        return downloadRemoteArchive(
                new URL(manifest.getString("package_url")),
                manifest.getLong("package_size"), manifest.getString("sha256"),
                "regulations-update.zip", MAX_PACKAGE_BYTES,
                "正在下载完整知识库…", listener, required);
    }

    private File downloadDelta(
            JSONObject delta, Listener listener, boolean required
    ) throws Exception {
        return downloadRemoteArchive(
                new URL(delta.getString("url")), delta.getLong("size"),
                delta.getString("sha256"), "regulations-delta.zip", MAX_DELTA_BYTES,
                "正在下载新增和变更内容…", listener, required);
    }

    private File downloadRemoteArchive(
            URL packageUrl, long expected, String expectedHash, String cacheName,
            long maximum, String progressMessage, Listener listener, boolean required
    ) throws Exception {
        if (!root.exists() && !root.mkdirs()) throw new IllegalStateException("无法建立制度存储目录");
        File archive = new File(context.getCacheDir(), cacheName);
        if (archive.length() > expected && !archive.delete()) {
            throw new IllegalStateException("无法清理异常的下载缓存");
        }
        Exception lastError = null;
        for (int attempt = 1; attempt <= 4 && archive.length() < expected; attempt++) {
            long offset = archive.length();
            try (InputStream input = new BufferedInputStream(openDownload(packageUrl, offset));
                 FileOutputStream file = new FileOutputStream(archive, offset > 0);
                 BufferedOutputStream output = new BufferedOutputStream(file)) {
                byte[] buffer = new byte[128 * 1024];
                long total = offset;
                int read;
                int lastPercent = -1;
                while ((read = input.read(buffer)) >= 0) {
                    total += read;
                    if (total > maximum || total > expected + 1024) {
                        throw new IllegalStateException("下载文件大小超过清单声明");
                    }
                    output.write(buffer, 0, read);
                    int percent = (int) Math.min(100, total * 100L / expected);
                    if (percent != lastPercent) {
                        lastPercent = percent;
                        listener.onProgress(progressMessage, percent, required);
                    }
                }
                output.flush();
                if (total != expected) throw new IllegalStateException("下载连接提前结束");
            } catch (RangeNotSupportedException error) {
                lastError = error;
                if (archive.exists() && !archive.delete()) {
                    throw new IllegalStateException("服务器不支持续传且无法重置下载缓存", error);
                }
            } catch (Exception error) {
                lastError = error;
            }
            if (archive.length() < expected && attempt < 4) {
                int percent = (int) Math.min(99, archive.length() * 100L / expected);
                listener.onProgress("网络中断，正在自动续传（第 " + (attempt + 1) + " 次）…", percent, required);
                Thread.sleep(1000L * attempt);
            }
        }
        if (archive.length() != expected) {
            throw new IllegalStateException(
                    "知识库下载在自动重试后仍未完成：" + readableMessage(lastError), lastError);
        }

        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(archive))) {
            byte[] buffer = new byte[128 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                digest.update(buffer, 0, read);
                }
        }
        String actual = hex(digest.digest());
        if (!actual.equalsIgnoreCase(expectedHash)) {
            if (archive.exists() && !archive.delete()) archive.deleteOnExit();
            throw new IllegalStateException("知识库更新包 SHA-256 校验失败");
        }
        return archive;
    }

    private File copyBundledPackage(
            JSONObject manifest, Listener listener, boolean required
    ) throws Exception {
        if (!root.exists() && !root.mkdirs()) throw new IllegalStateException("无法建立制度存储目录");
        File archive = new File(context.getCacheDir(), "bundled-knowledge-package.zip");
        long expected = manifest.getLong("package_size");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(
                    context.getAssets().open(OFFLINE_PACKAGE_ASSET));
             FileOutputStream file = new FileOutputStream(archive);
             BufferedOutputStream output = new BufferedOutputStream(file)) {
            byte[] buffer = new byte[128 * 1024];
            long total = 0;
            int read;
            int lastPercent = -1;
            while ((read = input.read(buffer)) >= 0) {
                total += read;
                if (total > MAX_PACKAGE_BYTES || total > expected + 1024) {
                    throw new IllegalStateException("内置知识库大小超过版本清单声明");
                }
                output.write(buffer, 0, read);
                digest.update(buffer, 0, read);
                int percent = (int) Math.min(100, total * 100L / expected);
                if (percent != lastPercent) {
                    lastPercent = percent;
                    listener.onProgress("正在读取内置知识库…", percent, required);
                }
            }
            output.flush();
            if (total != expected) throw new IllegalStateException("内置知识库大小与版本清单不一致");
        }
        String actual = hex(digest.digest());
        if (!actual.equalsIgnoreCase(manifest.getString("sha256"))) {
            if (archive.exists() && !archive.delete()) archive.deleteOnExit();
            throw new IllegalStateException("内置知识库 SHA-256 校验失败");
        }
        return archive;
    }

    private void installPackage(
            File archive, JSONObject manifest, Listener listener, boolean required,
            BooleanSupplier installAllowed
    ) throws Exception {
        File staging = new File(root, "staging-" + System.currentTimeMillis());
        deleteTree(staging);
        if (!staging.mkdirs()) throw new IllegalStateException("无法建立更新临时目录");
        try {
            listener.onProgress("正在安全解压知识库…", 100, required);
            extractZip(archive, staging);
            JSONObject installed = validateInstalledPackage(staging, manifest);

            if (BuildConfig.OFFLINE_BUILD) applyOfflineBranding(staging);
            activateStaging(staging, installed, installAllowed);
        } catch (Exception error) {
            deleteTree(staging);
            throw error;
        }
    }

    private void installDelta(
            File archive, JSONObject manifest, JSONObject publicDelta, Listener listener,
            boolean required, BooleanSupplier installAllowed
    ) throws Exception {
        long stamp = System.currentTimeMillis();
        File extracted = new File(root, "delta-extracted-" + stamp);
        File staging = new File(root, "staging-" + stamp);
        deleteTree(extracted);
        deleteTree(staging);
        if (!extracted.mkdirs() || !staging.mkdirs()) {
            throw new IllegalStateException("无法建立增量更新临时目录");
        }
        try {
            listener.onProgress("正在校验增量更新…", 100, required);
            extractZip(archive, extracted);
            JSONObject delta = readJson(new File(extracted, "delta.json"), 16L * 1024L * 1024L);
            validateDeltaMetadata(delta, publicDelta, manifest);
            JSONObject installedBefore = readJson(new File(current, "package.json"), 1024 * 1024);
            if (installedBefore.getLong("version_code") != delta.getLong("base_version_code")
                    || !installedBefore.getString("version").equals(delta.getString("base_version"))) {
                throw new IllegalStateException("本地知识库版本与增量包起点不一致");
            }

            listener.onProgress("正在合并新增和变更内容…", 100, required);
            cloneTree(current, staging);
            JSONArray deletions = delta.getJSONArray("delete");
            if (deletions.length() > MAX_ZIP_ENTRIES) {
                throw new IllegalStateException("增量删除文件数量异常");
            }
            for (int index = 0; index < deletions.length(); index++) {
                deleteTree(safeChild(staging, deletions.getString(index)));
            }
            JSONArray files = delta.getJSONArray("files");
            if (files.length() > MAX_ZIP_ENTRIES) {
                throw new IllegalStateException("增量更新文件数量异常");
            }
            File payload = new File(extracted, "payload");
            for (int index = 0; index < files.length(); index++) {
                JSONObject file = files.getJSONObject(index);
                String path = file.getString("path");
                File source = safeChild(payload, path);
                File destination = safeChild(staging, path);
                if (!source.isFile() || source.length() != file.getLong("size")
                        || !hashFile(source).equalsIgnoreCase(file.getString("sha256"))) {
                    throw new IllegalStateException("增量文件校验失败：" + path);
                }
                deleteTree(destination);
                File parent = destination.getParentFile();
                if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
                    throw new IllegalStateException("无法建立增量更新目录");
                }
                if (!source.renameTo(destination)) copyFile(source, destination);
            }
            JSONObject installed = validateInstalledPackage(staging, manifest);
            activateStaging(staging, installed, installAllowed);
        } finally {
            deleteTree(extracted);
            deleteTree(staging);
        }
    }

    private void validateDeltaMetadata(
            JSONObject delta, JSONObject publicDelta, JSONObject manifest
    ) throws Exception {
        if (delta.getInt("schema") != 1 || !"regulations".equals(delta.getString("scope"))
                || delta.getLong("base_version_code") != publicDelta.getLong("base_version_code")
                || !delta.getString("base_version").equals(publicDelta.getString("base_version"))
                || delta.getLong("target_version_code") != manifest.getLong("version_code")
                || !delta.getString("target_version").equals(manifest.getString("version"))) {
            throw new IllegalStateException("增量包版本信息与更新清单不一致");
        }
    }

    private JSONObject validateInstalledPackage(File packageRoot, JSONObject manifest) throws Exception {
        File packageJson = new File(packageRoot, "package.json");
        File index = new File(packageRoot, "index.html");
        if (!packageJson.isFile() || !index.isFile()) {
            throw new IllegalStateException("更新包缺少必要文件");
        }
        JSONObject installed = readJson(packageJson, 1024 * 1024);
        if (!"regulations".equals(installed.getString("scope"))
                || installed.getLong("version_code") != manifest.getLong("version_code")
                || installed.getInt("documents") != manifest.getInt("documents")
                || installed.optInt("regulation_documents", installed.getInt("documents"))
                    != manifest.optInt("regulation_documents", manifest.getInt("documents"))
                || installed.optInt("accounting_documents", 0)
                    != manifest.optInt("accounting_documents", 0)
                || installed.optInt("fiscal_documents", 0)
                    != manifest.optInt("fiscal_documents", 0)
                || installed.optInt("case_documents", 0) != manifest.optInt("case_documents", 0)) {
            throw new IllegalStateException("更新包内容与清单不一致");
        }
        return installed;
    }

    private void activateStaging(
            File staging, JSONObject installed, BooleanSupplier installAllowed
    ) throws Exception {
        ensureInstallAllowed(installAllowed);
        deleteTree(backup);
        ensureInstallAllowed(installAllowed);
        boolean movedCurrent = false;
        if (current.exists()) {
            if (!current.renameTo(backup)) throw new IllegalStateException("无法备份当前制度库");
            movedCurrent = true;
        }
        if (!staging.renameTo(current)) {
            if (movedCurrent) backup.renameTo(current);
            throw new IllegalStateException("无法启用新制度库");
        }
        saveInstalled(installed);
    }

    private static void ensureInstallAllowed(BooleanSupplier installAllowed) {
        if (!installAllowed.getAsBoolean()) {
            throw new IllegalStateException("APP 正在前台使用，后台更新将稍后重试");
        }
    }

    private void extractZip(File archive, File staging) throws Exception {
        String targetRoot = staging.getCanonicalPath() + File.separator;
        long extracted = 0;
        int entries = 0;
        try (ZipInputStream input = new ZipInputStream(
                new BufferedInputStream(new FileInputStream(archive)), StandardCharsets.UTF_8)) {
            ZipEntry entry;
            byte[] buffer = new byte[128 * 1024];
            while ((entry = input.getNextEntry()) != null) {
                entries += 1;
                if (entries > MAX_ZIP_ENTRIES) throw new IllegalStateException("更新包文件数量异常");
                File destination = new File(staging, entry.getName());
                if (!destination.getCanonicalPath().startsWith(targetRoot)) {
                    throw new IllegalStateException("更新包包含不安全路径");
                }
                if (entry.isDirectory()) {
                    if (!destination.isDirectory() && !destination.mkdirs()) {
                        throw new IllegalStateException("无法建立更新目录");
                    }
                } else {
                    File parent = destination.getParentFile();
                    if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
                        throw new IllegalStateException("无法建立更新目录");
                    }
                    try (FileOutputStream output = new FileOutputStream(destination)) {
                        int read;
                        while ((read = input.read(buffer)) >= 0) {
                            extracted += read;
                            if (extracted > MAX_EXTRACTED_BYTES) {
                                throw new IllegalStateException("更新包解压后大小异常");
                            }
                            output.write(buffer, 0, read);
                        }
                    }
                }
                input.closeEntry();
            }
        }
    }

    private void applyOfflineBranding(File staging) throws Exception {
        File homepage = new File(staging, "index.html");
        String html;
        try (FileInputStream input = new FileInputStream(homepage);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            copyLimited(input, output, 4L * 1024L * 1024L, null);
            html = output.toString(StandardCharsets.UTF_8.name());
        }
        String[][] replacements = {
                {"FINANCIAL, FISCAL, ACCOUNTING & CASES · ONLINE SYNC",
                        "FINANCIAL, FISCAL, ACCOUNTING & CASES · FULLY OFFLINE"},
                {"金融监管制度、财政监管制度、会计制度与财政、证监、审计、纪检监察案例，联网同步更新。",
                        "金融监管制度、财政监管制度、会计制度与财政、证监、审计、纪检监察案例，完整内置，无需联网。"},
                {"<div class=\"offline-badge\"><span></span>已联网同步</div>",
                        "<div class=\"offline-badge\"><span></span>完整离线</div>"},
                {"<strong>4</strong><span>套在线知识库</span>",
                        "<strong>4</strong><span>套离线知识库</span>"},
                {"<strong>联网同步版</strong>", "<strong>完整离线版</strong>"},
                {"<p>本地内容由 APP 从 GitHub 安全下载并校验；金融监管、财政监管、会计制度和案例库随同一版本自动更新。</p>",
                        "<p>金融监管、财政监管、会计制度和四来源案例完整内置于 APK；查询、阅读、分享和导出均无需联网。</p>"},
        };
        for (String[] replacement : replacements) {
            if (!html.contains(replacement[0])) {
                throw new IllegalStateException("离线版首页标记缺失，拒绝使用错误版本的数据包");
            }
            html = html.replace(replacement[0], replacement[1]);
        }
        try (FileOutputStream output = new FileOutputStream(homepage)) {
            output.write(html.getBytes(StandardCharsets.UTF_8));
        }
    }

    private InputStream openDownload(URL initial) throws Exception {
        return openDownload(initial, 0);
    }

    private InputStream openDownload(URL initial, long offset) throws Exception {
        URL url = initial;
        for (int redirect = 0; redirect < 6; redirect++) {
            if (!"https".equalsIgnoreCase(url.getProtocol())) {
                throw new IllegalArgumentException("网络更新只允许 HTTPS");
            }
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(20_000);
            connection.setReadTimeout(60_000);
            connection.setRequestProperty("Accept", "application/json, application/zip, */*");
            connection.setRequestProperty("User-Agent", "FinReg-Android/" + BuildConfig.VERSION_NAME);
            if (offset > 0) connection.setRequestProperty("Range", "bytes=" + offset + "-");
            int status = connection.getResponseCode();
            if (status >= 300 && status < 400) {
                String location = connection.getHeaderField("Location");
                connection.disconnect();
                if (location == null) throw new IllegalStateException("更新下载重定向无效");
                url = new URL(url, location);
                continue;
            }
            if (status < 200 || status >= 300) {
                connection.disconnect();
                throw new IllegalStateException("更新服务器返回 HTTP " + status);
            }
            if (offset > 0 && status != HttpURLConnection.HTTP_PARTIAL) {
                connection.disconnect();
                throw new RangeNotSupportedException();
            }
            return new ConnectionInputStream(connection);
        }
        throw new IllegalStateException("更新下载重定向次数过多");
    }

    private static File safeChild(File parent, String relative) throws Exception {
        if (relative == null || relative.isEmpty() || relative.startsWith("/")
                || relative.startsWith("\\") || relative.contains("\\")) {
            throw new IllegalStateException("增量包包含不安全路径");
        }
        String[] parts = relative.split("/", -1);
        for (String part : parts) {
            if (part.isEmpty() || ".".equals(part) || "..".equals(part)) {
                throw new IllegalStateException("增量包包含不安全路径");
            }
        }
        String parentPath = parent.getCanonicalPath() + File.separator;
        File child = new File(parent, relative);
        if (!child.getCanonicalPath().startsWith(parentPath)) {
            throw new IllegalStateException("增量包包含越界路径");
        }
        return child;
    }

    private static void cloneTree(File source, File destination) throws Exception {
        if (source.isDirectory()) {
            if (!destination.isDirectory() && !destination.mkdirs()) {
                throw new IllegalStateException("无法建立增量更新目录");
            }
            File[] children = source.listFiles();
            if (children == null) throw new IllegalStateException("无法读取当前知识库");
            for (File child : children) {
                cloneTree(child, new File(destination, child.getName()));
            }
            return;
        }
        File parent = destination.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IllegalStateException("无法建立增量更新目录");
        }
        try {
            Os.link(source.getAbsolutePath(), destination.getAbsolutePath());
        } catch (Exception ignored) {
            copyFile(source, destination);
        }
    }

    private static void copyFile(File source, File destination) throws Exception {
        try (InputStream input = new BufferedInputStream(new FileInputStream(source));
             FileOutputStream file = new FileOutputStream(destination);
             BufferedOutputStream output = new BufferedOutputStream(file)) {
            byte[] buffer = new byte[128 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
        }
    }

    private static String hashFile(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[128 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) digest.update(buffer, 0, read);
        }
        return hex(digest.digest());
    }

    private void saveInstalled(JSONObject installed) throws Exception {
        preferences.edit()
                .putString(KEY_VERSION, installed.getString("version"))
                .putLong(KEY_VERSION_CODE, installed.getLong("version_code"))
                .putInt(KEY_DOCUMENTS, installed.getInt("documents"))
                .putInt(KEY_REGULATION_DOCUMENTS,
                        installed.optInt("regulation_documents", installed.getInt("documents")))
                .putInt(KEY_ACCOUNTING_DOCUMENTS, installed.optInt("accounting_documents", 0))
                .putInt(KEY_FISCAL_DOCUMENTS, installed.optInt("fiscal_documents", 0))
                .putInt(KEY_CASE_DOCUMENTS, installed.optInt("case_documents", 0))
                .apply();
    }

    private JSONObject readJson(File file, long limit) throws Exception {
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            copyLimited(input, output, limit, null);
            return new JSONObject(output.toString(StandardCharsets.UTF_8.name()));
        }
    }

    private static void copyLimited(
            InputStream input, java.io.OutputStream output, long limit, MessageDigest digest
    ) throws Exception {
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int read;
        while ((read = input.read(buffer)) >= 0) {
            total += read;
            if (total > limit) throw new IllegalStateException("读取内容超过安全限制");
            output.write(buffer, 0, read);
            if (digest != null) digest.update(buffer, 0, read);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder output = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) output.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        return output.toString();
    }

    private static void deleteTree(File file) throws Exception {
        if (!file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) {
            for (File child : children) deleteTree(child);
        }
        if (!file.delete()) throw new IllegalStateException("无法清理旧更新文件：" + file.getName());
    }

    private static String readableMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty() ? error.getClass().getSimpleName() : message;
    }

    private static final class ConnectionInputStream extends InputStream {
        private final HttpURLConnection connection;
        private final InputStream input;

        ConnectionInputStream(HttpURLConnection connection) throws Exception {
            this.connection = connection;
            this.input = connection.getInputStream();
        }

        @Override public int read() throws java.io.IOException {
            return input.read();
        }

        @Override public int read(byte[] buffer, int offset, int length) throws java.io.IOException {
            return input.read(buffer, offset, length);
        }

        @Override public void close() throws java.io.IOException {
            try {
                input.close();
            } finally {
                connection.disconnect();
            }
        }
    }

    private static final class RangeNotSupportedException extends Exception {
        RangeNotSupportedException() {
            super("更新服务器未接受断点续传");
        }
    }
}
