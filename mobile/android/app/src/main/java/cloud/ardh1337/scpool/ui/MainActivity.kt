package cloud.ardh1337.scpool.ui

import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.TextUtils
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cloud.ardh1337.scpool.api.ScpoolApiClient
import cloud.ardh1337.scpool.data.OrderStatus
import cloud.ardh1337.scpool.data.ServiceItem
import cloud.ardh1337.scpool.databinding.ActivityMainBinding
import cloud.ardh1337.scpool.service.AutomationEngine
import cloud.ardh1337.scpool.service.FloatingOverlayService
import cloud.ardh1337.scpool.service.ScpoolAccessibilityService
import cloud.ardh1337.scpool.util.Prefs
import cloud.ardh1337.scpool.util.SoundHelper
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: Prefs
    private var availableServices: List<ServiceItem> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = Prefs(this)

        initViews()
        observeEngineState()
    }

    override fun onResume() {
        super.onResume()
        checkPermissions()
        updateServerInfo()
        loadAvailableServices()
    }

    private fun initViews() {
        binding.btnOpenSettings.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        binding.btnEnableAccessibility.setOnClickListener {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            startActivity(intent)
        }

        binding.btnStartAction.setOnClickListener {
            val isRunning = AutomationEngine.isRunning.value
            val current = AutomationEngine.currentOrder.value
            if (!isRunning) {
                val selected = prefs.selectedService.ifBlank { "whatsapp" }
                AutomationEngine.startOrder(this, selected)
            } else if (current?.status == OrderStatus.OTP_RECEIVED) {
                // Tombol berubah jadi minta kirim ulang OTP
                AutomationEngine.resendOtp(this)
            }
        }

        binding.btnCopyPhone.setOnClickListener {
            val order = AutomationEngine.currentOrder.value
            if (order != null && order.phoneNational.isNotBlank()) {
                SoundHelper.copyToClipboard(this, "Nomor", order.phoneNational)
                Toast.makeText(this, "Nomor disalin ke clipboard", Toast.LENGTH_SHORT).show()
            }
        }

        binding.btnCopyOtp.setOnClickListener {
            val order = AutomationEngine.currentOrder.value
            if (order?.otp != null) {
                SoundHelper.copyToClipboard(this, "OTP", order.otp)
                Toast.makeText(this, "Kode OTP disalin", Toast.LENGTH_SHORT).show()
            }
        }

        binding.btnCancelOrder.setOnClickListener {
            AutomationEngine.cancelOrder(this)
        }

        binding.btnFinishOrder.setOnClickListener {
            AutomationEngine.finishOrder(this)
        }
    }

    private fun checkPermissions() {
        // Cek Aksesibilitas
        val hasAccessibility = isAccessibilityServiceEnabled()
        binding.bannerAccessibility.visibility = if (hasAccessibility) View.GONE else View.VISIBLE

        // Cek Izin Overlay jika diaktifkan di preferensi
        if (prefs.showOverlay && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            )
            Toast.makeText(this, "Mohon berikan izin Tampil di Atas Aplikasi Lain untuk widget", Toast.LENGTH_LONG).show()
            startActivity(intent)
        }
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val expected = ComponentName(this, ScpoolAccessibilityService::class.java)
        val enabledServices = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false

        val splitter = TextUtils.SimpleStringSplitter(':')
        splitter.setString(enabledServices)
        while (splitter.hasNext()) {
            val componentStr = splitter.next()
            val component = ComponentName.unflattenFromString(componentStr)
            if (component != null && component == expected) {
                return true
            }
        }
        return false
    }

    private fun updateServerInfo() {
        val host = Uri.parse(prefs.serverUrl).host ?: prefs.serverUrl
        binding.tvDeviceInfo.text = "${prefs.deviceName} • $host"
    }

    private fun loadAvailableServices() {
        lifecycleScope.launch {
            try {
                val api = ScpoolApiClient(prefs)
                availableServices = api.getServices()
                populateServiceSpinner(availableServices)
            } catch (_: Exception) {
                // Fallback default jika server belum terjangkau
                val defaults = listOf(
                    ServiceItem("whatsapp", "WhatsApp", 0),
                    ServiceItem("telegram", "Telegram", 0),
                    ServiceItem("google", "Google", 0),
                    ServiceItem("shopee", "Shopee", 0),
                    ServiceItem("gojek", "Gojek", 0),
                    ServiceItem("dana", "DANA", 0),
                    ServiceItem("tiktok", "TikTok", 0)
                )
                populateServiceSpinner(defaults)
            }
        }
    }

    private fun populateServiceSpinner(services: List<ServiceItem>) {
        val labels = services.map {
            if (it.activeSims > 0) "${it.displayName} (${it.activeSims} SIM)" else it.displayName
        }
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        binding.spinnerService.adapter = adapter

        val currentIdx = services.indexOfFirst { it.service.equals(prefs.selectedService, ignoreCase = true) }
        if (currentIdx >= 0) {
            binding.spinnerService.setSelection(currentIdx)
        }

        binding.spinnerService.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (position in services.indices) {
                    prefs.selectedService = services[position].service
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    private fun observeEngineState() {
        // Pantau pesanan aktif
        lifecycleScope.launch {
            AutomationEngine.currentOrder.collectLatest { order ->
                if (order == null) {
                    binding.tvActivePhone.text = "Belum ada nomor"
                    binding.tvOrderTimer.text = "--:--"
                    binding.layoutOtpBox.visibility = View.GONE
                    binding.btnCancelOrder.visibility = View.GONE
                    binding.btnFinishOrder.visibility = View.GONE
                    binding.btnStartAction.text = "Ambil Nomor & Otomasi"
                    binding.btnStartAction.isEnabled = true
                } else {
                    binding.tvActivePhone.text = order.phoneNational.ifBlank { order.phoneNumber }
                    val mins = order.secondsLeft / 60
                    val secs = order.secondsLeft % 60
                    binding.tvOrderTimer.text = String.format("%02d:%02d", mins, secs)

                    when (order.status) {
                        OrderStatus.ACTIVE -> {
                            binding.layoutOtpBox.visibility = View.GONE
                            binding.btnCancelOrder.visibility = View.VISIBLE
                            binding.btnFinishOrder.visibility = View.GONE
                            binding.btnStartAction.text = "Menunggu SMS Masuk…"
                            binding.btnStartAction.isEnabled = false
                        }
                        OrderStatus.OTP_RECEIVED -> {
                            binding.layoutOtpBox.visibility = View.VISIBLE
                            binding.tvActiveOtp.text = order.otp ?: "------"
                            binding.tvOtpSender.text = "Pengirim: ${order.sms?.sender ?: "-"}"
                            binding.btnCancelOrder.visibility = View.GONE
                            binding.btnFinishOrder.visibility = View.VISIBLE
                            binding.btnStartAction.text = "Kirim Ulang OTP"
                            binding.btnStartAction.isEnabled = true
                        }
                        OrderStatus.DONE, OrderStatus.CANCELLED, OrderStatus.EXPIRED -> {
                            binding.btnCancelOrder.visibility = View.GONE
                            binding.btnFinishOrder.visibility = View.GONE
                            binding.btnStartAction.text = "Ambil Nomor Baru"
                            binding.btnStartAction.isEnabled = true
                        }
                        else -> {}
                    }
                }
            }
        }

        // Pantau status badge
        lifecycleScope.launch {
            AutomationEngine.statusText.collectLatest { status ->
                binding.tvMainStatusBadge.text = status
            }
        }

        // Pantau log real-time
        lifecycleScope.launch {
            AutomationEngine.logs.collectLatest { logs ->
                if (logs.isNotEmpty()) {
                    binding.tvTerminalLog.text = logs.joinToString("\n")
                }
            }
        }
    }
}

