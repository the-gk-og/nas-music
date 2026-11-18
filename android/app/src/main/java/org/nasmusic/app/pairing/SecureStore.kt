package org.nasmusic.app.pairing

import android.content.Context
import android.util.Base64
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.RegistryConfiguration
import com.google.crypto.tink.aead.AeadConfig
import com.google.crypto.tink.integration.android.AndroidKeysetManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import org.nasmusic.app.net.ServerConfig

/**
 * Stores the [ServerConfig] (device token, CF Access secret, cert pin, addresses) encrypted with
 * AES-256-GCM. The Tink keyset itself is wrapped by a non-exportable Android Keystore key.
 */
class SecureStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }

    private val aead: Aead by lazy {
        AeadConfig.register()
        AndroidKeysetManager.Builder()
            .withSharedPref(context, KEYSET_NAME, KEYSET_PREFS)
            .withKeyTemplate(KeyTemplates.get("AES256_GCM"))
            .withMasterKeyUri(MASTER_KEY_URI)
            .build()
            .keysetHandle
            .getPrimitive(RegistryConfiguration.get(), Aead::class.java)
    }

    private val _config = MutableStateFlow(load())
    val config: StateFlow<ServerConfig?> = _config.asStateFlow()

    private fun load(): ServerConfig? {
        val blob = prefs.getString(KEY_CONFIG, null) ?: return null
        return runCatching {
            val plain = aead.decrypt(Base64.decode(blob, Base64.NO_WRAP), AD)
            json.decodeFromString(ServerConfig.serializer(), plain.decodeToString())
        }.getOrNull()
    }

    fun save(config: ServerConfig?) {
        if (config == null) {
            prefs.edit().remove(KEY_CONFIG).apply()
        } else {
            val plain = json.encodeToString(ServerConfig.serializer(), config).encodeToByteArray()
            val enc = Base64.encodeToString(aead.encrypt(plain, AD), Base64.NO_WRAP)
            prefs.edit().putString(KEY_CONFIG, enc).apply()
        }
        _config.value = config
    }

    private companion object {
        const val PREFS = "secure_store"
        const val KEY_CONFIG = "server_config"
        const val KEYSET_NAME = "nasmusic_keyset"
        const val KEYSET_PREFS = "nasmusic_keyset_prefs"
        const val MASTER_KEY_URI = "android-keystore://nasmusic_master_key"
        val AD = "nasmusic.server_config.v1".encodeToByteArray()
    }
}
