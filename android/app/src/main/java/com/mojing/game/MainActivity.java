package com.mojing.game;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
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
import java.io.ByteArrayInputStream;
import java.util.Collections;

public final class MainActivity extends Activity {
    private static final int CHOOSE_DOCUMENT = 1;
    private static final int BACKGROUND = Color.rgb(23, 30, 29);
    private WebView web;
    private TextView status;
    private boolean pageFailed;
    private ValueCallback<Uri[]> fileSelection;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
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
        web.loadUrl(MojingApplication.URL);
    }

    private static boolean isGameUri(Uri uri) {
        return "http".equals(uri.getScheme()) && MojingApplication.HOST.equals(uri.getHost())
            && uri.getPort() == MojingApplication.PORT && uri.getUserInfo() == null;
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == CHOOSE_DOCUMENT && fileSelection != null) {
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
