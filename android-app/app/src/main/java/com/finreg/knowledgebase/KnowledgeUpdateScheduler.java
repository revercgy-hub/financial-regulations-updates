package com.finreg.knowledgebase;

import android.annotation.SuppressLint;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;

final class KnowledgeUpdateScheduler {
    static final int JOB_ID = 1702001;
    static final String PREFS = "background_knowledge_updates";
    static final String KEY_LAST_CHECK = "last_check";
    static final String KEY_LAST_RESULT = "last_result";
    static final String KEY_LAST_VERSION = "last_version";

    private static final String KEY_SCHEDULED_APP_VERSION = "scheduled_app_version";
    private static final long INTERVAL_MILLIS = 24L * 60L * 60L * 1000L;
    private static final long FLEX_MILLIS = 6L * 60L * 60L * 1000L;
    private static volatile boolean appForeground;

    private KnowledgeUpdateScheduler() {}

    @SuppressLint("MissingPermission") // Offline builds return before the persisted online job is created.
    static void schedule(Context context) {
        if (BuildConfig.OFFLINE_BUILD) return;
        Context appContext = context.getApplicationContext();
        JobScheduler scheduler = appContext.getSystemService(JobScheduler.class);
        if (scheduler == null) return;

        SharedPreferences preferences = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        JobInfo existing = scheduler.getPendingJob(JOB_ID);
        if (existing != null
                && preferences.getInt(KEY_SCHEDULED_APP_VERSION, 0) == BuildConfig.VERSION_CODE) {
            return;
        }

        if (existing != null) scheduler.cancel(JOB_ID);
        JobInfo job = new JobInfo.Builder(
                JOB_ID, new ComponentName(appContext, KnowledgeUpdateJobService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .setPeriodic(INTERVAL_MILLIS, FLEX_MILLIS)
                .build();
        if (scheduler.schedule(job) == JobScheduler.RESULT_SUCCESS) {
            preferences.edit()
                    .putInt(KEY_SCHEDULED_APP_VERSION, BuildConfig.VERSION_CODE)
                    .apply();
        }
    }

    static void setAppForeground(boolean foreground) {
        appForeground = foreground;
    }

    static boolean isAppForeground() {
        return appForeground;
    }
}
