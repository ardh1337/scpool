package cloud.ardh1337.scpool.util

import android.content.Context
import android.content.SharedPreferences

class Prefs(context: Context) {
    private val sp: SharedPreferences = context.getSharedPreferences("scpool_prefs", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("server_url", "https://scpool.ardh1337.cloud") ?: "https://scpool.ardh1337.cloud"
        set(value) = sp.edit().putString("server_url", value.trim().removeSuffix("/")).apply()

    var apiKey: String
        get() = sp.getString("api_key", "") ?: ""
        set(value) = sp.edit().putString("api_key", value.trim()).apply()

    var deviceName: String
        get() = sp.getString("device_name", "HP-01") ?: "HP-01"
        set(value) = sp.edit().putString("device_name", value.trim()).apply()

    var selectedService: String
        get() = sp.getString("selected_service", "whatsapp") ?: "whatsapp"
        set(value) = sp.edit().putString("selected_service", value.trim()).apply()

    var autoLaunchApp: Boolean
        get() = sp.getBoolean("auto_launch_app", true)
        set(value) = sp.edit().putBoolean("auto_launch_app", value).apply()

    var autoInputPhone: Boolean
        get() = sp.getBoolean("auto_input_phone", true)
        set(value) = sp.edit().putBoolean("auto_input_phone", value).apply()

    var autoInputOtp: Boolean
        get() = sp.getBoolean("auto_input_otp", true)
        set(value) = sp.edit().putBoolean("auto_input_otp", value).apply()

    var autoCopyClipboard: Boolean
        get() = sp.getBoolean("auto_copy_clipboard", true)
        set(value) = sp.edit().putBoolean("auto_copy_clipboard", value).apply()

    var showOverlay: Boolean
        get() = sp.getBoolean("show_overlay", true)
        set(value) = sp.edit().putBoolean("show_overlay", value).apply()

    var autoRetry: Boolean
        get() = sp.getBoolean("auto_retry", true)
        set(value) = sp.edit().putBoolean("auto_retry", value).apply()

    /** Jumlah nomor yang dicoba per otomasi sebelum berhenti. */
    var maxAttempts: Int
        get() = sp.getInt("max_attempts", 3)
        set(value) = sp.edit().putInt("max_attempts", value).apply()

    /** Detik menunggu OTP sebelum menekan "Kirim ulang SMS" di WhatsApp. */
    var resendAfterSec: Int
        get() = sp.getInt("resend_after_sec", 70)
        set(value) = sp.edit().putInt("resend_after_sec", value).apply()

    var soundVibrate: Boolean
        get() = sp.getBoolean("sound_vibrate", true)
        set(value) = sp.edit().putBoolean("sound_vibrate", value).apply()
}

