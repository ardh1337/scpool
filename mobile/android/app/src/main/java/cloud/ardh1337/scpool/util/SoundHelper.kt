package cloud.ardh1337.scpool.util

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.media.RingtoneManager
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

object SoundHelper {

    fun notifyOtpReceived(context: Context) {
        // Mainkan nada dering notifikasi sistem
        try {
            val alert = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            val ringtone = RingtoneManager.getRingtone(context, alert)
            ringtone?.play()
        } catch (_: Exception) {
        }

        // Getarkan perangkat
        try {
            val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
                vm?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }

            vibrator?.let {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    it.vibrate(
                        VibrationEffect.createWaveform(
                            longArrayOf(0, 200, 100, 200, 100, 300),
                            -1
                        )
                    )
                } else {
                    @Suppress("DEPRECATION")
                    it.vibrate(longArrayOf(0, 200, 100, 200, 100, 300), -1)
                }
            }
        } catch (_: Exception) {
        }
    }

    fun copyToClipboard(context: Context, label: String, text: String): Boolean {
        return try {
            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
            val clip = ClipData.newPlainText(label, text)
            cm?.setPrimaryClip(clip)
            true
        } catch (_: Exception) {
            false
        }
    }
}

