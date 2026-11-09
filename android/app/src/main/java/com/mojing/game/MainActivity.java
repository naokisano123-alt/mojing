package com.mojing.game;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Base64;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.HttpAuthHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

public final class MainActivity extends Activity {
    private static final int CHOOSE_DOCUMENT = 1;
    private static final int SAVE_WORLD_EXPORT = 2;
    private static final String PENDING_EXPORT = "pendingWorldExport";
    private static final int BACKGROUND = Color.rgb(23, 30, 29);
    private WebView web;
    private TextView status;
    private boolean pageFailed;
    private ValueCallback<Uri[]> fileSelection;
    private String pendingWorldExport;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (savedInstanceState != null) pendingWorldExport = savedInstanceState.getString(PENDING_EXPORT);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BACKGROUND);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                var padding = insets.getInsets(WindowInsets.Type.systemBars()
                    | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(padding.left, padding.top, padding.right, padding.bottom);
                return insets;
            });
        }

        web = new WebView(this);
        web.setVisibility(View.INVISIBLE);
        web.setBackgroundColor(BACKGROUND);
        WebView.setWebContentsDebuggingEnabled(false);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        status = new TextView(this);
        status.setText("正在打开墨境…");
        status.setTextColor(Color.WHITE);
        status.setTextSize(17);
        status.setGravity(Gravity.CENTER);
        status.setPadding(24, 0, 24, 0);
        root.addView(status, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        ((MojingApplication) getApplication()).engine().whenComplete((password, failure) ->
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed()) return;
                if (failure != null) {
                    Log.e("Mojing", "Local engine startup failed", failure);
                    status.setText("墨境启动失败。请关闭应用后重新打开。");
                    return;
                }
                openGame(password);
            }));
    }

    private void openGame(String password) {
        web.setWebViewClient(new WebViewClient() {
            @Override public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler,
                    String host, String realm) {
                if (("Mojing local".equals(realm)) && (MojingApplication.HOST.equals(host)
                        || (MojingApplication.HOST + ":" + MojingApplication.PORT).equals(host))) {
                    handler.proceed("player", password);
                } else {
                    handler.cancel();
                }
            }

            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (request.isForMainFrame() && "GET".equals(request.getMethod()) && isWorldExport(request.getUrl())) {
                    chooseExportDestination(request.getUrl());
                    return true;
                }
                return !isGameUri(request.getUrl());
            }

            @Override public WebResourceResponse shouldInterceptRequest(WebView view,
                    WebResourceRequest request) {
                if (isGameUri(request.getUrl())) return null;
                return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden",
                    Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }

            @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                pageFailed = false;
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (!pageFailed && isGameUri(Uri.parse(url))) {
                    status.setVisibility(View.GONE);
                    web.setVisibility(View.VISIBLE);
                }
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request,
                    WebResourceError error) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    status.setText("无法打开本机游戏。请关闭应用后重新打开。");
                    status.setVisibility(View.VISIBLE);
                    web.setVisibility(View.INVISIBLE);
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                if (fileSelection != null) fileSelection.onReceiveValue(null);
                fileSelection = callback;
                try {
                    startActivityForResult(params.createIntent(), CHOOSE_DOCUMENT);
                } catch (ActivityNotFoundException e) {
                    fileSelection = null;
                    callback.onReceiveValue(null);
                }
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mimeType, length) -> {
            Uri uri = Uri.parse(url);
            if (isWorldExport(uri)) chooseExportDestination(uri);
        });
        web.loadUrl(MojingApplication.URL);
    }

    private static boolean isGameUri(Uri uri) {
        return "http".equals(uri.getScheme()) && MojingApplication.HOST.equals(uri.getHost())
            && uri.getPort() == MojingApplication.PORT && uri.getUserInfo() == null;
    }

    private static boolean isWorldExport(Uri uri) {
        String path = uri.getEncodedPath();
        String query = uri.getEncodedQuery();
        return isGameUri(uri) && uri.getFragment() == null && path != null
            && path.matches("/api/v1/worlds/[0-9a-fA-F-]{36}/export")
            && (query == null || "include_saves=true".equals(query));
    }

    private void chooseExportDestination(Uri url) {
        if (pendingWorldExport != null) return;
        pendingWorldExport = url.toString();
        String id = url.getPathSegments().get(3).substring(0, 8);
        String suffix = url.getEncodedQuery() == null ? "-settings.json" : "-with-saves.json";
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/json")
            .putExtra(Intent.EXTRA_TITLE, "mojing-world-" + id + suffix);
        try {
            startActivityForResult(intent, SAVE_WORLD_EXPORT);
        } catch (ActivityNotFoundException e) {
            pendingWorldExport = null;
            Toast.makeText(this, "无法打开系统文件保存界面。", Toast.LENGTH_LONG).show();
        }
    }

    private void saveWorldExport(String url, Uri destination) {
        if (!isWorldExport(Uri.parse(url)) || !"content".equals(destination.getScheme())) return;
        MojingApplication app = (MojingApplication) getApplication();
        app.engine().thenAcceptAsync(password -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(url).openConnection(Proxy.NO_PROXY);
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(30000);
                connection.setInstanceFollowRedirects(false);
                String credentials = Base64.encodeToString(("player:" + password)
                    .getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                connection.setRequestProperty("Authorization", "Basic " + credentials);
                if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) {
                    throw new IOException("Export request failed");
                }
                try (InputStream input = connection.getInputStream();
                     OutputStream output = app.getContentResolver().openOutputStream(destination, "w")) {
                    if (output == null) throw new IOException("No export output stream");
                    byte[] buffer = new byte[16384];
                    int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                }
            } catch (IOException e) {
                throw new IllegalStateException("World export failed", e);
            } finally {
                if (connection != null) connection.disconnect();
            }
        })
            .whenComplete((ignored, failure) -> runOnUiThread(() -> Toast.makeText(app,
                failure == null ? "世界文件已导出。" : "世界文件未能完整保存，请重试。", Toast.LENGTH_LONG).show()));
    }

    @Override protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        outState.putString(PENDING_EXPORT, pendingWorldExport);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == SAVE_WORLD_EXPORT) {
            String url = pendingWorldExport;
            pendingWorldExport = null;
            if (resultCode == RESULT_OK && url != null && data != null && data.getData() != null) {
                saveWorldExport(url, data.getData());
            }
        } else if (requestCode == CHOOSE_DOCUMENT && fileSelection != null) {
            ValueCallback<Uri[]> callback = fileSelection;
            fileSelection = null;
            callback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
        }
    }

    @Override public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (fileSelection != null) fileSelection.onReceiveValue(null);
        if (web.getParent() instanceof FrameLayout) ((FrameLayout) web.getParent()).removeView(web);
        web.destroy();
        super.onDestroy();
    }
}
