package cloud.ardh1337.scpool.service

import android.content.Context
import android.content.Intent
import android.os.SystemClock
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
    private lateinit var appContext: Context

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

    private var attempt = 0
    private var service = ""
    private var closedOrderId = 0  // pesanan yang hasilnya sudah dilaporkan (cegah lapor dobel dari event berulang)

    // Pemetaan nama paket Android untuk peluncuran otomatis
    val TARGET_PACKAGES = mapOf(
        "whatsapp" to listOf("com.whatsapp"),
        "whatsapp_business" to listOf("com.whatsapp.w4b"),
        "telegram" to listOf("org.telegram.messenger", "org.thunderdog.challegram"),
        "shopee" to listOf("com.shopee.id"),
        "gojek" to listOf("com.gojek.app"),
        "dana" to listOf("id.dana"),
        "tiktok" to listOf("com.zhiliaoapp.musically"),
        "facebook" to listOf("com.facebook.katana", "com.facebook.lite")
    )

    fun isWhatsApp(service: String) = service == "whatsapp" || service == "whatsapp_business"

    fun addLog(msg: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        _logs.value = (_logs.value + "[$time] $msg").takeLast(100)
    }

    // ------------------------------------------------------------------------------------------ mulai & ulang
    fun startOrder(context: Context, service: String) {
        if (_isRunning.value) return
        appContext = context.applicationContext
        val prefs = Prefs(appContext)
        if (prefs.apiKey.isBlank()) {
            addLog("ERROR: Kunci API (X-API-Key) belum diisi di Pengaturan!")
            return
        }
        this.service = service
        attempt = 0
        WhatsAppFlow.reset()
        _isRunning.value = true
        if (prefs.showOverlay) FloatingOverlayService.start(appContext)
        orderNext(launchApp = prefs.autoLaunchApp)
    }

    /** Pesan nomor (percobaan pertama atau pengganti nomor yang gagal). */
    private fun orderNext(launchApp: Boolean) {
        val prefs = Prefs(appContext)
        val api = ScpoolApiClient(prefs)
        attempt += 1
        phoneFilled = false
        otpFilled = false
        _statusText.value = "MEMESAN NOMOR…"
        addLog("Memesan nomor untuk '$service' (percobaan $attempt/${maxAttempts(prefs)})...")

        scope.launch {
            try {
                val order = api.createOrder(service, prefs.deviceName.ifBlank { "HP-01" }, minutes = 10)
                _currentOrder.value = order
                _statusText.value = "MENUNGGU OTP"
                addLog("Nomor diperoleh: ${order.phoneNational} (ID: ${order.orderId})")
                if (prefs.autoCopyClipboard) SoundHelper.copyToClipboard(appContext, "Nomor SCPool", order.phoneNational)
                if (launchApp) launchTargetApp(appContext, service)
                ScpoolAccessibilityService.fillNow()
                startPolling(api, order.orderId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _statusText.value = "GAGAL"
                addLog("Gagal memesan: ${e.message}")
                stopSession()
            }
        }
    }

    private fun maxAttempts(prefs: Prefs) = if (prefs.autoRetry) prefs.maxAttempts.coerceIn(1, 10) else 1

    // ------------------------------------------------------------------------------------------ tunggu OTP
    private fun startPolling(api: ScpoolApiClient, orderId: Int, afterSmsId: Int? = null) {
        pollJob?.cancel()
        val prefs = Prefs(appContext)
        val resendAfter = prefs.resendAfterSec.coerceIn(30, 300) * 1000L
        val startedAt = SystemClock.elapsedRealtime()
        var resends = 0

        pollJob = scope.launch {
            addLog("Menunggu SMS OTP masuk dari modem pool...")
            while (isActive && _isRunning.value) {
                // WhatsApp: ketuk "Kirim ulang SMS" bila OTP belum datang, lalu nyatakan gagal bila tetap tidak datang.
                val elapsed = SystemClock.elapsedRealtime() - startedAt
                if (afterSmsId == null && isWhatsApp(service)) {
                    // Batas akhir dihitung dari awal pesanan, jadi tetap tercapai walau tombol kirim ulang tak ketemu.
                    if (elapsed >= resendAfter * (MAX_RESENDS + 1) + NO_SMS_GRACE_MS) {
                        addLog("OTP tidak datang setelah kirim ulang.")
                        reportFailure("no_sms")
                        break
                    }
                    if (resends < MAX_RESENDS && elapsed >= resendAfter * (resends + 1) &&
                        ScpoolAccessibilityService.requestResend()) {
                        resends += 1
                        addLog("OTP belum datang, menekan 'Kirim ulang SMS' ($resends/$MAX_RESENDS).")
                    }
                }
                val wait = if (isWhatsApp(service)) 15 else 30
                try {
                    val order = api.getOrder(orderId, wait = wait, afterSmsId = afterSmsId)
                    _currentOrder.value = order
                    // Saat kirim ulang, server mengembalikan OTP lama bila `wait` habis tanpa kode baru.
                    val isOldOtp = afterSmsId != null && (order.sms?.id ?: 0) <= afterSmsId
                    when (order.status) {
                        OrderStatus.OTP_RECEIVED -> if (isOldOtp) continue else {
                            if (!order.otp.isNullOrBlank()) {
                                _statusText.value = "KODE DITERIMA"
                                addLog("OTP Masuk: ${order.otp} (Pengirim: ${order.sms?.sender ?: "-"})")
                                if (prefs.autoCopyClipboard) SoundHelper.copyToClipboard(appContext, "OTP SCPool", order.otp)
                                if (prefs.soundVibrate) SoundHelper.notifyOtpReceived(appContext)
                                // Layar OTP biasanya diam (tak ada event aksesibilitas): isi sekarang juga.
                                ScpoolAccessibilityService.fillNow()
                            }
                            break  // selanjutnya: berhasil/gagal dikenali dari layar, atau tombol Selesai
                        }
                        OrderStatus.DONE, OrderStatus.CANCELLED -> {
                            _statusText.value = if (order.status == OrderStatus.DONE) "SELESAI" else "DIBATALKAN"
                            addLog("Pesanan #${order.orderId} ditutup di server.")
                            stopSession()
                            break
                        }
                        OrderStatus.EXPIRED -> {
                            addLog("Waktu pesanan #${order.orderId} habis tanpa OTP.")
                            closedOrderId = order.orderId
                            retryOrStop("KADALUWARSA")
                            break
                        }
                        else -> {}
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    if (e.code in 400..499) {
                        _statusText.value = "GAGAL"
                        addLog("Pesanan tidak bisa dilanjutkan: ${e.message}")
                        stopSession()
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

    // ------------------------------------------------------------------------------------------ hasil
    /** Dipanggil saat layar menunjukkan kegagalan (diblokir, dibatasi, kode salah) atau OTP tak kunjung datang. */
    fun reportFailure(reason: String) {
        val order = _currentOrder.value ?: return
        if (!_isRunning.value || closedOrderId == order.orderId) return
        closedOrderId = order.orderId
        pollJob?.cancel()
        addLog("Nomor ${order.phoneNational} gagal: ${REASON_LABEL[reason] ?: reason}.")
        val api = ScpoolApiClient(Prefs(appContext))
        scope.launch {
            try {
                val closed = if (order.status == OrderStatus.OTP_RECEIVED)
                    api.finishOrder(order.orderId, result = "failed", reason = reason)
                else api.cancelOrder(order.orderId, reason = reason)
                _currentOrder.value = closed
            } catch (e: Exception) {
                addLog("Gagal melapor ke server: ${e.message}")
            }
            retryOrStop("GAGAL")
        }
    }

    /** Dipanggil saat WhatsApp sudah melewati verifikasi (layar profil / pulihkan cadangan). */
    fun reportSuccess() {
        val order = _currentOrder.value ?: return
        if (!_isRunning.value || closedOrderId == order.orderId) return
        closedOrderId = order.orderId
        pollJob?.cancel()
        val api = ScpoolApiClient(Prefs(appContext))
        scope.launch {
            try {
                _currentOrder.value = api.finishOrder(order.orderId, result = "success")
                _statusText.value = "BERHASIL"
                addLog("Verifikasi ${order.phoneNational} berhasil. Pesanan selesai.")
            } catch (e: Exception) {
                addLog("Gagal melapor ke server: ${e.message}")
            } finally {
                stopSession()
            }
        }
    }

    private fun retryOrStop(finalStatus: String) {
        val prefs = Prefs(appContext)
        if (_isRunning.value && attempt < maxAttempts(prefs)) {
            addLog("Mengambil nomor pengganti...")
            scope.launch {
                delay(1500)
                if (_isRunning.value) orderNext(launchApp = false)
            }
        } else {
            _statusText.value = finalStatus
            if (attempt >= maxAttempts(prefs)) addLog("Batas $attempt percobaan tercapai, otomasi berhenti.")
            stopSession()
        }
    }

    // ------------------------------------------------------------------------------------------ tombol manual
    fun resendOtp(context: Context) {
        val order = _currentOrder.value ?: return
        appContext = context.applicationContext
        addLog("Meminta kirim ulang OTP untuk pesanan #${order.orderId}...")
        otpFilled = false
        _statusText.value = "MENUNGGU OTP BARU"
        if (isWhatsApp(order.service)) ScpoolAccessibilityService.requestResend()
        startPolling(ScpoolApiClient(Prefs(appContext)), order.orderId, afterSmsId = order.sms?.id)
    }

    fun finishOrder(context: Context) {
        val order = _currentOrder.value ?: return
        appContext = context.applicationContext
        closedOrderId = order.orderId
        pollJob?.cancel()
        scope.launch {
            try {
                addLog("Menyelesaikan pesanan #${order.orderId}...")
                _currentOrder.value = ScpoolApiClient(Prefs(appContext)).finishOrder(order.orderId)
                _statusText.value = "SELESAI"
                addLog("Pesanan berhasil diselesaikan.")
            } catch (e: Exception) {
                addLog("Gagal menyelesaikan: ${e.message}")
            } finally {
                stopSession()
            }
        }
    }

    fun cancelOrder(context: Context) {
        val order = _currentOrder.value ?: return
        appContext = context.applicationContext
        closedOrderId = order.orderId
        pollJob?.cancel()
        scope.launch {
            try {
                addLog("Membatalkan pesanan #${order.orderId}...")
                _currentOrder.value = ScpoolApiClient(Prefs(appContext)).cancelOrder(order.orderId)
                _statusText.value = "DIBATALKAN"
                addLog("Pesanan dibatalkan. Kunci nomor dilepas.")
            } catch (e: Exception) {
                addLog("Gagal membatalkan: ${e.message}")
            } finally {
                stopSession()
            }
        }
    }

    /** Otomasi berakhir: tutup widget melayang beserta notifikasi foreground-nya. */
    private fun stopSession() {
        _isRunning.value = false
        if (::appContext.isInitialized) FloatingOverlayService.stop(appContext)
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

    private const val MAX_RESENDS = 1
    private const val NO_SMS_GRACE_MS = 30_000L
    private val REASON_LABEL = mapOf(
        "no_sms" to "OTP tidak datang", "banned" to "nomor diblokir WhatsApp",
        "limited" to "dibatasi sementara", "wrong_code" to "kode ditolak"
    )
}
