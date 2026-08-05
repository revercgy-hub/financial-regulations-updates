package com.finreg.knowledgebase;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.content.Context;
import android.content.SharedPreferences;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public final class KnowledgeUpdateJobService extends JobService {
    private volatile RunState currentRun;

    @Override public boolean onStartJob(JobParameters parameters) {
        if (KnowledgeUpdateScheduler.isAppForeground()) {
            recordResult("APP 正在前台，启动检查已接管本次更新", null);
            return false;
        }

        RunState run = new RunState(parameters, Executors.newSingleThreadExecutor());
        currentRun = run;
        RegulationUpdater updater = new RegulationUpdater(this, run.executor);
        updater.check(false, () -> !KnowledgeUpdateScheduler.isAppForeground(),
                new RegulationUpdater.Listener() {
                    @Override public void onChecking(boolean required) {
                        recordResult("正在后台检查制度库和案例库", null);
                    }

                    @Override public void onDownloadStarted(
                            String version, long bytes, boolean required
                    ) {
                        recordResult("正在后台下载知识库 " + version, version);
                    }

                    @Override public void onProgress(
                            String message, int percent, boolean required
                    ) {}

                    @Override public void onReady(
                            boolean installed, String version, int documents
                    ) {
                        finish(run, false, "已自动更新制度库和案例库", version);
                    }

                    @Override public void onNoUpdate(
                            String version, int documents, boolean manual
                    ) {
                        finish(run, false, "制度库和案例库已是最新版本", version);
                    }

                    @Override public void onError(
                            String message, boolean hasUsablePackage, boolean manual
                    ) {
                        finish(run, true, "后台更新未完成：" + message, updater.getVersion());
                    }
                });
        return true;
    }

    @Override public boolean onStopJob(JobParameters parameters) {
        RunState run = currentRun;
        if (run != null) {
            run.completed.set(true);
            run.executor.shutdownNow();
        }
        recordResult("后台任务被系统暂停，将自动重试", null);
        return true;
    }

    private void finish(
            RunState run, boolean reschedule, String result, String version
    ) {
        if (!run.completed.compareAndSet(false, true)) return;
        recordResult(result, version);
        jobFinished(run.parameters, reschedule);
        run.executor.shutdown();
        if (currentRun == run) currentRun = null;
    }

    private void recordResult(String result, String version) {
        SharedPreferences.Editor editor = getSharedPreferences(
                KnowledgeUpdateScheduler.PREFS, Context.MODE_PRIVATE).edit()
                .putLong(KnowledgeUpdateScheduler.KEY_LAST_CHECK, System.currentTimeMillis())
                .putString(KnowledgeUpdateScheduler.KEY_LAST_RESULT, result);
        if (version != null) {
            editor.putString(KnowledgeUpdateScheduler.KEY_LAST_VERSION, version);
        }
        editor.apply();
    }

    private static final class RunState {
        private final JobParameters parameters;
        private final ExecutorService executor;
        private final AtomicBoolean completed = new AtomicBoolean(false);

        private RunState(JobParameters parameters, ExecutorService executor) {
            this.parameters = parameters;
            this.executor = executor;
        }
    }
}
