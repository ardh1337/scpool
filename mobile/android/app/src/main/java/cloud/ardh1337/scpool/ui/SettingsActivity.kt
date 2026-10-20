package cloud.ardh1337.scpool.ui

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import cloud.ardh1337.scpool.databinding.ActivitySettingsBinding
import cloud.ardh1337.scpool.util.Prefs

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var prefs: Prefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = Prefs(this)
        loadPreferences()

        binding.btnBack.setOnClickListener {
            finish()
        }

        binding.btnSaveSettings.setOnClickListener {
            savePreferences()
        }
    }

    private fun loadPreferences() {
        binding.etServerUrl.setText(prefs.serverUrl)
        binding.etApiKey.setText(prefs.apiKey)
        binding.etDeviceName.setText(prefs.deviceName)

        binding.switchAutoLaunch.isChecked = prefs.autoLaunchApp
        binding.switchAutoPhone.isChecked = prefs.autoInputPhone
        binding.switchAutoOtp.isChecked = prefs.autoInputOtp
        binding.switchAutoClipboard.isChecked = prefs.autoCopyClipboard
        binding.switchShowOverlay.isChecked = prefs.showOverlay
        binding.switchSoundVibrate.isChecked = prefs.soundVibrate
        binding.switchAutoRetry.isChecked = prefs.autoRetry
        binding.etMaxAttempts.setText(prefs.maxAttempts.toString())
        binding.etResendAfter.setText(prefs.resendAfterSec.toString())
    }

    private fun savePreferences() {
        val serverUrl = binding.etServerUrl.text?.toString()?.trim().orEmpty()
        val apiKey = binding.etApiKey.text?.toString()?.trim().orEmpty()
        val deviceName = binding.etDeviceName.text?.toString()?.trim().orEmpty()

        if (serverUrl.isBlank()) {
            Toast.makeText(this, "URL Server tidak boleh kosong", Toast.LENGTH_SHORT).show()
            return
        }

        prefs.serverUrl = serverUrl
        prefs.apiKey = apiKey
        prefs.deviceName = deviceName.ifBlank { "HP-01" }

        prefs.autoLaunchApp = binding.switchAutoLaunch.isChecked
        prefs.autoInputPhone = binding.switchAutoPhone.isChecked
        prefs.autoInputOtp = binding.switchAutoOtp.isChecked
        prefs.autoCopyClipboard = binding.switchAutoClipboard.isChecked
        prefs.showOverlay = binding.switchShowOverlay.isChecked
        prefs.soundVibrate = binding.switchSoundVibrate.isChecked
        prefs.autoRetry = binding.switchAutoRetry.isChecked
        prefs.maxAttempts = (binding.etMaxAttempts.text?.toString()?.toIntOrNull() ?: 3).coerceIn(1, 10)
        prefs.resendAfterSec = (binding.etResendAfter.text?.toString()?.toIntOrNull() ?: 70).coerceIn(30, 300)

        Toast.makeText(this, "Pengaturan berhasil disimpan", Toast.LENGTH_SHORT).show()
        finish()
    }
}

