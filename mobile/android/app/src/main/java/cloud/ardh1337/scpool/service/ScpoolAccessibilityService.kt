package cloud.ardh1337.scpool.service

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import cloud.ardh1337.scpool.data.OrderStatus
import cloud.ardh1337.scpool.util.Prefs

class ScpoolAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile private var instance: ScpoolAccessibilityService? = null

        /** Isi nomor/OTP sekarang tanpa menunggu layar berubah. Aman dipanggil dari thread mana pun. */
        fun fillNow() {
            val service = instance ?: return
            service.mainHandler.post { service.tryFill(service.rootInActiveWindow?.packageName?.toString()) }
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        tryFill(event.packageName?.toString())
    }

    private fun tryFill(currentPackage: String?) {
        if (!AutomationEngine.isRunning.value) return

        val order = AutomationEngine.currentOrder.value ?: return
        val prefs = Prefs(this)

        val targetPackages = AutomationEngine.TARGET_PACKAGES[order.service.lowercase()] ?: return
        if (currentPackage == null || !targetPackages.contains(currentPackage)) return

        val rootNode = rootInActiveWindow ?: return

        // 1. Auto-input Nomor Telepon
        if (prefs.autoInputPhone && !AutomationEngine.phoneFilled && order.status == OrderStatus.ACTIVE) {
            val phoneFilled = tryInputPhoneNumber(rootNode, order.phoneNational, order.phoneNumber)
            if (phoneFilled) {
                AutomationEngine.phoneFilled = true
                AutomationEngine.addLog("Aksesibilitas: Berhasil mengisi nomor telepon ke layar.")
            }
        }

        // 2. Auto-input Kode OTP
        if (prefs.autoInputOtp && !AutomationEngine.otpFilled &&
            order.status == OrderStatus.OTP_RECEIVED && !order.otp.isNullOrBlank()) {
            val otpFilled = tryInputOtp(rootNode, order.otp)
            if (otpFilled) {
                AutomationEngine.otpFilled = true
                AutomationEngine.addLog("Aksesibilitas: Berhasil mengisi kode OTP ${order.otp} ke layar.")
            }
        }
    }

    private fun tryInputPhoneNumber(root: AccessibilityNodeInfo, phoneNational: String, phoneFull: String): Boolean {
        val editTexts = mutableListOf<AccessibilityNodeInfo>()
        collectNodesByClass(root, "android.widget.EditText", editTexts)

        if (editTexts.isEmpty()) return false

        // Pola 1: WhatsApp (Dua input: [Kode Negara (62)] dan [Nomor Telepon])
        if (editTexts.size >= 2) {
            val field1 = editTexts[0]
            val field2 = editTexts[1]

            val text1 = field1.text?.toString() ?: ""
            if (text1.contains("62") || text1.contains("+62") || text1.isEmpty()) {
                // Pastikan kode negara 62 terisi
                if (!text1.contains("62")) {
                    setNodeText(field1, "62")
                }
                // Nomor diisi tanpa awalan 0 (misal: 81234567890)
                val stripped = if (phoneNational.startsWith("0")) phoneNational.substring(1) else phoneNational
                val success = setNodeText(field2, stripped)
                if (success) {
                    clickNextButton(root)
                    return true
                }
            }
        }

        // Pola 2: Satu input tunggal (Telegram, dll)
        if (editTexts.size == 1) {
            val field = editTexts[0]
            // Coba masukkan nomor nasional atau internasional
            val numberToInput = if (phoneNational.startsWith("08")) phoneNational else phoneFull
            val success = setNodeText(field, numberToInput)
            if (success) {
                clickNextButton(root)
                return true
            }
        }

        return false
    }

    private fun tryInputOtp(root: AccessibilityNodeInfo, otp: String): Boolean {
        val editTexts = mutableListOf<AccessibilityNodeInfo>()
        collectNodesByClass(root, "android.widget.EditText", editTexts)

        if (editTexts.isEmpty()) return false

        // Pola 1: Kotak terpisah 4 - 6 digit
        if (editTexts.size == otp.length) {
            var allSet = true
            for (i in otp.indices) {
                val ok = setNodeText(editTexts[i], otp[i].toString())
                if (!ok) allSet = false
            }
            return allSet
        }

        // Pola 2: Input tunggal untuk seluruh kode OTP
        if (editTexts.size == 1) {
            return setNodeText(editTexts[0], otp)
        }

        return false
    }

    private fun setNodeText(node: AccessibilityNodeInfo, text: String): Boolean {
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun clickNextButton(root: AccessibilityNodeInfo) {
        val keywords = listOf("lanjut", "next", "berikutnya", "oke", "ok", "verifikasi", "kirim")
        val buttons = mutableListOf<AccessibilityNodeInfo>()
        collectNodesByClass(root, "android.widget.Button", buttons)

        for (btn in buttons) {
            val label = (btn.text?.toString() ?: btn.contentDescription?.toString() ?: "").lowercase()
            if (keywords.any { label.contains(it) }) {
                btn.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                return
            }
        }
    }

    private fun collectNodesByClass(
        node: AccessibilityNodeInfo?,
        targetClass: String,
        results: MutableList<AccessibilityNodeInfo>
    ) {
        if (node == null) return
        if (node.className?.toString() == targetClass) {
            results.add(node)
        }
        for (i in 0 until node.childCount) {
            collectNodesByClass(node.getChild(i), targetClass, results)
        }
    }

    override fun onInterrupt() {
        // Layanan terganggu oleh sistem
    }
}

