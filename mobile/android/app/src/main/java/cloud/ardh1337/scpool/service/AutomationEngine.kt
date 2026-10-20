package cloud.ardh1337.scpool.service

import android.content.Context
import android.content.Intent
import cloud.ardh1337.scpool.api.ApiException
import cloud.ardh1337.scpool.api.ScpoolApiClient
import cloud.ardh1337.scpool.data.OrderData
import cloud.ardh1337.scpool.data.OrderStatus
import cloud.ardh1337.scpool.util.Prefs
import cloud.ardh1337.scpool.util.SoundHelper
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object AutomationEngine {

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var pollJob: Job? = null

    private val _currentOrder = MutableStateFlow<OrderData?>(null)
    val currentOrder = _currentOrder.asStateFlow()

    private val _statusText = MutableStateFlow("SIAP")
    val statusText = _statusText.asStateFlow()

    private val _isRunning = MutableStateFlow(false)
    val isRunning = _isRunning.asStateFlow()

    private val _logs = MutableStateFlow<List<String>>(emptyList())
    val logs = _logs.asStateFlow()

    // Flag koordinasi dengan AccessibilityService
    @Volatile var phoneFilled: Boolean = false
    @Volatile var otpFilled: Boolean = false

    // Pemetaan nama paket Android untuk peluncuran otomatis
    val TARGET_PACKAGES = mapOf(
        "whatsapp" to listOf("com.whatsapp", "com.whatsapp.w4b"),
        "telegram" to listOf("org.telegram.messenger", "org.thunderdog.challegram"),
        "shopee" to listOf("com.shopee.id"),
        "gojek" to listOf("com.gojek.app"),
        "dana" to listOf("id.dana"),
        "tiktok" to listOf("com.zhiliaoapp.musically"),
        "facebook" to listOf("com.facebook.katana", "com.facebook.lite")
    )

    fun addLog(msg: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        val entry = "[$time] $msg"
        val updated = (_logs.value + entry).takeLast(100)
        _logs.value = updated
    }

    fun startOrder(context: Context, service: String) {
        if (_isRunning.value) return
        val prefs = Prefs(context)
        if (prefs.apiKey.isBlank()) {
            addLog("ERROR: Kunci API (X-API-Key) belum diisi di Pengaturan!")
            return
        }

        val api = ScpoolApiClient(prefs)
        _isRunning.value = true
        _statusText.value = "MEMESAN NOMOR…"
        phoneFilled = false
        otpFilled = false
        addLog("Memesan nomor baru untuk '$service'...")

        scope.launch {
            try {
                val order = api.createOrder(
                    service = service,
                    device = prefs.deviceName.ifBlank { "HP-01" },
                    minutes = 10
                )
                _currentOrder.value = order
                _statusText.value = "MENUNGGU OTP"
                addLog("Nomor diperoleh: ${order.phoneNational} (ID: ${order.orderId})")

                if (prefs.autoCopyClipboard) {
                    SoundHelper.copyToClipboard(context, "Nomor SCPool", order.phoneNational)
                    addLog("Nomor ${order.phoneNational} disalin ke clipboard")
                }

                if (prefs.showOverlay) {
                    FloatingOverlayService.start(context)
                }

                if (prefs.autoLaunchApp) {
                    launchTargetApp(context, service)
                }

                startPolling(context, api, order.orderId)
            } catch (e: Exception) {
                _isRunning.value = false
                _statusText.value = "GAGAL"
                addLog("Gagal memesan: ${e.message}")
            }
        }
    }

    private fun startPolling(context: Context, api: ScpoolApiClient, orderId: Int, afterSmsId: Int? = null) {
        pollJob?.cancel()
        val prefs = Prefs(context)

        pollJob = scope.launch {
            addLog("Menunggu SMS OTP masuk dari modem pool...")
            while (isActive && _isRunning.value) {
                try {
                    val order = api.getOrder(orderId, wait = 30, afterSmsId = afterSmsId)
                    _currentOrder.value = order

                    // Saat kirim ulang, server mengembalikan OTP lama bila `wait` habis tanpa kode baru.
                    val isOldOtp = afterSmsId != null && (order.sms?.id ?: 0) <= afterSmsId
                    when (order.status) {
                        OrderStatus.OTP_RECEIVED -> if (isOldOtp) continue else {
                            if (!order.otp.isNullOrBlank()) {
                                _statusText.value = "KODE DITERIMA"
                                addLog("OTP Masuk: ${order.otp} (Pengirim: ${order.sms?.sender ?: "-"})")

                                if (prefs.autoCopyClipboard) {
                                    SoundHelper.copyToClipboard(context, "OTP SCPool", order.otp)
                                }
                                if (prefs.soundVibrate) {
                                    SoundHelper.notifyOtpReceived(context)
                                }
                                // Layar OTP biasanya diam (tak ada event aksesibilitas): isi sekarang juga.
                                ScpoolAccessibilityService.fillNow()
                            }
                            // Tunggu aksi pengguna (selesai/batal/kirim ulang)
                            break
                        }
                        OrderStatus.DONE -> {
                            _statusText.value = "SELESAI"
                            addLog("Pesanan #${order.orderId} selesai.")
                            stopSession(context)
                            break
                        }
                        OrderStatus.CANCELLED -> {
                            _statusText.value = "DIBATALKAN"
                            addLog("Pesanan #${order.orderId} dibatalkan.")
                            stopSession(context)
                            break
                        }
                        OrderStatus.EXPIRED -> {
                            _statusText.value = "KADALUWARSA"
                            addLog("Waktu pesanan #${order.orderId} habis tanpa OTP.")
                            stopSession(context)
                            break
                        }
                        OrderStatus.ACTIVE -> {
                            // Server time-out dari wait=30, lanjut polling lagi
                        }
                        else -> {}
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    if (e.code in 400..499) {
                        _statusText.value = "GAGAL"
                        addLog("Pesanan tidak bisa dilanjutkan: ${e.message}")
                        stopSession(context)
                        break
                    }
                    addLog("Server error, mencoba lagi: ${e.message}")
                    delay(2000)
                } catch (e: Exception) {
                    if (isActive && _isRunning.value) {
                        addLog("Koneksi terputus, mencoba lagi: ${e.message}")
                        delay(2000)
                    }
                }
            }
        }
    }

    fun resendOtp(context: Context) {
        val order = _currentOrder.value ?: return
        val lastSmsId = order.sms?.id
        addLog("Meminta kirim ulang OTP untuk pesanan #${order.orderId}...")
        otpFilled = false
        _statusText.value = "MENUNGGU OTP BARU"
        val api = ScpoolApiClient(Prefs(context))
        startPolling(context, api, order.orderId, afterSmsId = lastSmsId)
    }

    fun finishOrder(context: Context) {
        val order = _currentOrder.value ?: return
        val api = ScpoolApiClient(Prefs(context))
        pollJob?.cancel()

        scope.launch {
            try {
                addLog("Menyelesaikan pesanan #${order.orderId}...")
                val res = api.finishOrder(order.orderId)
                _currentOrder.value = res
                _statusText.value = "SELESAI"
                addLog("Pesanan berhasil diselesaikan.")
            } catch (e: Exception) {
                addLog("Gagal menyelesaikan: ${e.message}")
            } finally {
                stopSession(context)
            }
        }
    }

    fun cancelOrder(context: Context) {
        val order = _currentOrder.value ?: return
        val api = ScpoolApiClient(Prefs(context))
        pollJob?.cancel()

        scope.launch {
            try {
                addLog("Membatalkan pesanan #${order.orderId}...")
                val res = api.cancelOrder(order.orderId)
                _currentOrder.value = res
                _statusText.value = "DIBATALKAN"
                addLog("Pesanan dibatalkan. Kunci nomor dilepas.")
            } catch (e: Exception) {
                addLog("Gagal membatalkan: ${e.message}")
            } finally {
                stopSession(context)
            }
        }
    }

    /** Pesanan berakhir: tutup widget melayang beserta notifikasi foreground-nya. */
    private fun stopSession(context: Context) {
        _isRunning.value = false
        FloatingOverlayService.stop(context)
    }

    fun launchTargetApp(context: Context, service: String) {
        val candidates = TARGET_PACKAGES[service.lowercase()] ?: return
        val pm = context.packageManager
        for (pkg in candidates) {
            val intent = pm.getLaunchIntentForPackage(pkg)
            if (intent != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                addLog("Membuka aplikasi $pkg...")
                return
            }
        }
        addLog("Aplikasi untuk '$service' tidak ditemukan di HP.")
    }
}

