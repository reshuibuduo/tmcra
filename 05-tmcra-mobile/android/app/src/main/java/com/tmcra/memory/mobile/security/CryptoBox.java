package com.tmcra.memory.mobile.security;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.io.ByteArrayOutputStream;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Android-Keystore envelope used for credentials and a separate local voiceprint key. */
public final class CryptoBox {
    private static final String KEY_STORE = "AndroidKeyStore";
    private static final String DEFAULT_KEY_ALIAS = "tmcra_mobile_private_v1";
    private static final int TAG_BITS = 128;
    private final String keyAlias;

    public CryptoBox() {
        this(DEFAULT_KEY_ALIAS);
    }

    public CryptoBox(String keyAlias) {
        if (keyAlias == null || !keyAlias.matches("[A-Za-z0-9_.:-]{8,96}")) {
            throw new IllegalArgumentException("Invalid Android Keystore alias");
        }
        this.keyAlias = keyAlias;
    }

    public byte[] encrypt(byte[] plaintext) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] iv = cipher.getIV();
        byte[] ciphertext = cipher.doFinal(plaintext);
        ByteArrayOutputStream output = new ByteArrayOutputStream(2 + iv.length + ciphertext.length);
        output.write(1);
        output.write(iv.length);
        output.write(iv);
        output.write(ciphertext);
        return output.toByteArray();
    }

    public byte[] decrypt(byte[] envelope) throws Exception {
        if (envelope == null || envelope.length < 15 || envelope[0] != 1) {
            throw new IllegalArgumentException("Invalid encrypted envelope");
        }
        int ivLength = envelope[1] & 0xff;
        if (ivLength < 12 || 2 + ivLength >= envelope.length) {
            throw new IllegalArgumentException("Invalid encrypted envelope");
        }
        byte[] iv = new byte[ivLength];
        System.arraycopy(envelope, 2, iv, 0, ivLength);
        int cipherLength = envelope.length - 2 - ivLength;
        byte[] ciphertext = new byte[cipherLength];
        System.arraycopy(envelope, 2 + ivLength, ciphertext, 0, cipherLength);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(TAG_BITS, iv));
        return cipher.doFinal(ciphertext);
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance(KEY_STORE);
        store.load(null);
        if (store.containsAlias(keyAlias)) {
            return (SecretKey) store.getKey(keyAlias, null);
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEY_STORE);
        generator.init(new KeyGenParameterSpec.Builder(
                keyAlias,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }
}
