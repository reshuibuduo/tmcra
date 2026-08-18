package com.tmcra.memory.mobile.security;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

public final class SessionStore {
    private static final String PREFERENCES = "tmcra_private_session";
    private static final String SESSION_KEY = "encrypted_cookie";
    private static final Pattern COOKIE = Pattern.compile(
            "^__Host-tmcra_session=[A-Za-z0-9_-]{20,180}$");

    private final SharedPreferences preferences;
    private final CryptoBox cryptoBox;

    public SessionStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        cryptoBox = new CryptoBox();
    }

    public synchronized void save(String cookie) throws Exception {
        if (cookie == null || !COOKIE.matcher(cookie).matches()) {
            throw new IllegalArgumentException("Invalid TMCRA session cookie");
        }
        byte[] encrypted = cryptoBox.encrypt(cookie.getBytes(StandardCharsets.UTF_8));
        preferences.edit()
                .putString(SESSION_KEY, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .apply();
    }

    public synchronized String load() {
        String encoded = preferences.getString(SESSION_KEY, null);
        if (encoded == null || encoded.isEmpty()) return null;
        try {
            byte[] decrypted = cryptoBox.decrypt(Base64.decode(encoded, Base64.NO_WRAP));
            String cookie = new String(decrypted, StandardCharsets.UTF_8);
            if (!COOKIE.matcher(cookie).matches()) throw new IllegalArgumentException("Invalid session");
            return cookie;
        } catch (Exception error) {
            clear();
            return null;
        }
    }

    public synchronized void clear() {
        preferences.edit().remove(SESSION_KEY).apply();
    }

    public boolean hasSession() {
        return load() != null;
    }
}
