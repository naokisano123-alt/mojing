package com.mojing.game;

import android.app.Application;
import android.content.res.AssetManager;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.CompletableFuture;

public final class MojingApplication extends Application {
    static final int PORT = BuildConfig.APPLICATION_ID.endsWith(".playtest") ? 8797 : 8796;
    static final String HOST = "127.0.0.1";
    static final String URL = "http://" + HOST + ":" + PORT + "/";
    private CompletableFuture<String> engine;

    synchronized CompletableFuture<String> engine() {
        if (engine == null) {
            engine = CompletableFuture.supplyAsync(this::startEngine);
        }
        return engine;
    }

    private String startEngine() {
        File web = new File(getCacheDir(), "mojing-web");
        try {
            copyAsset("web", web);
        } catch (IOException e) {
            throw new IllegalStateException("无法读取应用内游戏资源", e);
        }
        Python.start(new AndroidPlatform(this));
        return Python.getInstance().getModule("mojing_android")
            .callAttr("start", web.getAbsolutePath(),
                new File(getFilesDir(), "adventures").getAbsolutePath(), PORT, new ModelProfile(this))
            .toJava(String.class);
    }

    private void copyAsset(String path, File destination) throws IOException {
        AssetManager assets = getAssets();
        String[] entries = assets.list(path);
        if (entries != null && entries.length > 0) {
            if (!destination.mkdirs() && !destination.isDirectory()) {
                throw new IOException("无法创建游戏资源目录");
            }
            for (String name : entries) {
                copyAsset(path + "/" + name, new File(destination, name));
            }
            return;
        }
        try (InputStream source = assets.open(path);
             FileOutputStream target = new FileOutputStream(destination)) {
            byte[] buffer = new byte[16384];
            int length;
            while ((length = source.read(buffer)) != -1) {
                target.write(buffer, 0, length);
            }
        }
    }
}
