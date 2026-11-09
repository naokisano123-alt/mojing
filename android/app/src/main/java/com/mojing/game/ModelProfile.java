package com.mojing.game;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Only the in-process Python engine can call this store. */
public final class ModelProfile {
    private static final String ALIAS = "mojing-model-profile-v1";
    private static final String ENTRY = "profile";
    private final SharedPreferences preferences;

    ModelProfile(Context context) {
        preferences = context.getSharedPreferences("model_profile", Context.MODE_PRIVATE);
    }

    private SecretKey key(boolean create) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        SecretKey key = (SecretKey) store.getKey(ALIAS, null);
        if (key != null || !create) return key;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setKeySize(256)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build());
        return generator.generateKey();
    }

    public synchronized void save(String profile) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key(true));
        byte[] iv = cipher.getIV();
        byte[] encrypted = cipher.doFinal(profile.getBytes(StandardCharsets.UTF_8));
        byte[] payload = Arrays.copyOf(iv, iv.length + encrypted.length);
        System.arraycopy(encrypted, 0, payload, iv.length, encrypted.length);
        if (iv.length != 12 || !preferences.edit().putString(ENTRY, Base64.encodeToString(payload, Base64.NO_WRAP)).commit()) {
            throw new IOException("Cannot save model profile");
        }
    }

    public synchronized String load() throws Exception {
        String encoded = preferences.getString(ENTRY, null);
        if (encoded == null) return null;
        byte[] payload = Base64.decode(encoded, Base64.NO_WRAP);
        SecretKey key = key(false);
        if (key == null || payload.length < 28) throw new IOException("Model profile unavailable");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, payload, 0, 12));
        return new String(cipher.doFinal(payload, 12, payload.length - 12), StandardCharsets.UTF_8);
    }

    public synchronized void clear() throws IOException {
        if (!preferences.edit().remove(ENTRY).commit()) throw new IOException("Cannot delete model profile");
    }
}
