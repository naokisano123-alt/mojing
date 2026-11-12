package com.mojing.game;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.DocumentsContract;
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
import java.util.ArrayList;
import java.util.Collections;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

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
                    Intent intent = params.createIntent();
                    if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    }
                    startActivityForResult(intent, CHOOSE_DOCUMENT);
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
        if (!isGameUri(uri) || uri.getFragment() != null || path == null) return false;
        if (path.matches("/api/v1/worlds/[0-9a-fA-F-]{36}/export")) {
            return query == null || "include_saves=true".equals(query);
        }
        if (!path.matches("/api/v1/worlds/[0-9a-fA-F-]{36}/transfer") || query == null) return false;
        java.util.Map<String, java.util.List<String>> parameters = new java.util.HashMap<>();
        for (String name : uri.getQueryParameterNames()) {
            parameters.put(name, uri.getQueryParameters(name));
        }
        return WorldExportPolicy.allows(parameters);
    }

    private static boolean exportPair(Uri url) {
        return "folder".equals(url.getQueryParameter("destination"));
    }

    private void chooseExportDestination(Uri url) {
        if (pendingWorldExport != null) return;
        pendingWorldExport = url.toString();
        Intent intent;
        if (exportPair(url)) {
            intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        } else {
            String id = url.getPathSegments().get(3).substring(0, 8);
            String part = url.getQueryParameter("part");
            boolean zip = "zip".equals(part);
            String suffix = zip ? "-世界迁移.zip" : "saves".equals(part) ? "-存档.json" : "-世界观.json";
            if (part == null && url.getEncodedQuery() != null) suffix = "-含存档.json";
            intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType(zip ? "application/zip" : "application/json")
                .putExtra(Intent.EXTRA_TITLE, "墨境-" + id + suffix);
        }
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
                try (InputStream input = connection.getInputStream()) {
                    if (exportPair(Uri.parse(url))) {
                        saveWorldPair(app, destination, connection.getHeaderField("Content-Disposition"), input);
                    } else {
                        copyExport(app, destination, input);
                    }
                }
            } catch (IOException e) {
                throw new IllegalStateException("World export failed", e);
            } finally {
                if (connection != null) connection.disconnect();
            }
        })
            .whenComplete((ignored, failure) -> runOnUiThread(() -> Toast.makeText(app,
                failure == null ? (exportPair(Uri.parse(url)) ? "世界观和存档已分别保存。" : "世界文件已导出。")
                    : "世界文件未能完整保存，请重试。", Toast.LENGTH_LONG).show()));
    }

    private static void copyExport(MojingApplication app, Uri destination, InputStream input) throws IOException {
        try (OutputStream output = app.getContentResolver().openOutputStream(destination, "w")) {
            if (output == null) throw new IOException("No export output stream");
            byte[] buffer = new byte[16384];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
        }
    }

    private static void saveWorldPair(MojingApplication app, Uri tree, String disposition, InputStream input)
            throws IOException {
        Uri directory = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
        String stem = "墨境";
        String marker = "filename*=UTF-8''";
        int offset = disposition == null ? -1 : disposition.indexOf(marker);
        if (offset >= 0) {
            String name = Uri.decode(disposition.substring(offset + marker.length()));
            if (name.endsWith("-世界迁移.zip")) stem = name.substring(0, name.length() - "-世界迁移.zip".length());
        }
        ArrayList<Uri> created = new ArrayList<>();
        try (ZipInputStream archive = new ZipInputStream(input)) {
            for (String member : new String[] { "world.json", "saves.json" }) {
                ZipEntry entry = archive.getNextEntry();
                if (entry == null || !member.equals(entry.getName())) throw new IOException("Incomplete export pair");
                String suffix = member.equals("world.json") ? "-世界观.json" : "-存档.json";
                Uri file = DocumentsContract.createDocument(app.getContentResolver(), directory,
                    "application/json", stem + suffix);
                if (file == null) throw new IOException("No destination file");
                created.add(file);
                copyExport(app, file, archive);
                archive.closeEntry();
            }
            if (archive.getNextEntry() != null) throw new IOException("Unexpected export entry");
        } catch (IOException | RuntimeException failure) {
            for (Uri file : created) {
                try {
                    DocumentsContract.deleteDocument(app.getContentResolver(), file);
                } catch (IOException | RuntimeException ignored) {
                    // Some document providers do not permit deleting an incomplete file.
                }
            }
            throw failure;
        }
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
            ClipData selection = resultCode == RESULT_OK && data != null ? data.getClipData() : null;
            if (selection == null) {
                callback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            } else {
                ArrayList<Uri> files = new ArrayList<>();
                for (int index = 0; index < selection.getItemCount(); index++) {
                    Uri uri = selection.getItemAt(index).getUri();
                    if (uri != null && "content".equals(uri.getScheme())) files.add(uri);
                }
                callback.onReceiveValue(files.isEmpty() ? null : files.toArray(new Uri[0]));
            }
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
