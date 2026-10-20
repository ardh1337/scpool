package cloud.ardh1337.scpool.service

import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import cloud.ardh1337.scpool.data.OrderStatus

/**
 * Langkah pendaftaran WhatsApp / WhatsApp Business yang dijalankan lewat aksesibilitas:
 * setuju -> isi nomor -> konfirmasi -> pilih SMS -> isi OTP -> kenali berhasil, diblokir, atau dibatasi.
 *
 * Layar dikenali dari ID view WhatsApp lebih dulu, lalu dari teks (Indonesia & Inggris) sebagai cadangan karena ID
 * bisa berubah antar versi WhatsApp.
 */
object WhatsAppFlow {

    private val BANNED = listOf(
        "dilarang menggunakan whatsapp", "tidak diizinkan menggunakan whatsapp",
        "banned from using whatsapp", "not allowed to use whatsapp"
    )

    // Hanya pembatasan dalam hitungan jam. Hitung mundur "kirim ulang dalam 0:59" di layar verifikasi bukan blokir.
    private val LIMITED = Regex("(coba lagi|try again|tunggu|wait)[^\\n]{0,60}?\\d+\\s*(jam|hour)")

    private val WRONG_CODE = listOf(
        "kode yang anda masukkan salah", "kode salah", "kode tidak valid",
        "wrong code", "incorrect code", "code you entered is incorrect", "invalid code"
    )
    private val SUCCESS = listOf("info profil", "profile info", "pulihkan cadangan", "restore backup", "restore your chats")
    private val AGREE = listOf("setuju dan lanjutkan", "agree and continue")
    private val NEXT = listOf("berikutnya", "lanjut", "next")
    private val CONFIRM = listOf("ya", "yes", "ok", "oke")
    private val WRONG_NUMBER = listOf("nomor salah?", "wrong number?", "ubah nomor", "edit number")
    private val CHOOSE_SMS = listOf("terima sms", "kirim sms", "receive sms", "send sms", "sms")
    private val CONTINUE = listOf("lanjutkan", "continue")
    private val RESEND = listOf("kirim ulang sms", "kirim ulang kode", "resend sms", "resend code")

    /** Nomor yang terakhir diisi ke layar WhatsApp; beda dengan nomor pesanan = perlu kembali ke layar nomor. */
    @Volatile var filledPhone: String? = null
    @Volatile private var lastActionAt = 0L

    fun handle(root: AccessibilityNodeInfo, pkg: String) {
        val order = AutomationEngine.currentOrder.value ?: return
        if (SystemClock.uptimeMillis() - lastActionAt < 700) return  // beri waktu layar berganti setelah aksi
        val text = screenText(root)

        when {
            BANNED.any { text.contains(it) } -> {
                AutomationEngine.reportFailure("banned")
                clickByText(root, CONFIRM)
                acted()
            }
            LIMITED.containsMatchIn(text) -> {
                AutomationEngine.reportFailure("limited")
                clickByText(root, CONFIRM)
                acted()
            }
            AutomationEngine.otpFilled && WRONG_CODE.any { text.contains(it) } -> {
                AutomationEngine.reportFailure("wrong_code")
                acted()
            }
            AutomationEngine.otpFilled && (SUCCESS.any { text.contains(it) } ||
                byId(root, pkg, "registration_name") != null) -> {
                AutomationEngine.reportSuccess()
                acted()
            }
            clickById(root, pkg, "eula_accept") || clickByText(root, AGREE) -> acted()
            byId(root, pkg, "registration_phone") != null -> fillPhone(root, pkg, order.phoneNational, order.phoneNumber)
            isConfirmDialog(text, order.phoneNational) -> if (clickById(root, "android", "button1") ||
                clickByText(root, CONFIRM)) acted()
            filledPhone != null && filledPhone != order.phoneNumber -> {
                // Pesanan berganti (nomor sebelumnya gagal): kembali ke layar isi nomor.
                if (clickByText(root, WRONG_NUMBER)) acted()
            }
            isMethodChooser(text) -> {
                if (clickByText(root, CHOOSE_SMS)) {
                    clickByText(root, CONTINUE)
                    acted()
                }
            }
            order.status == OrderStatus.OTP_RECEIVED && !order.otp.isNullOrBlank() && !AutomationEngine.otpFilled -> {
                if (fillOtp(root, pkg, order.otp)) {
                    AutomationEngine.otpFilled = true
                    AutomationEngine.addLog("WhatsApp: kode OTP ${order.otp} diisi.")
                    acted()
                }
            }
        }
    }

    /** Ketuk "Kirim ulang SMS" bila tombolnya sudah aktif (hitung mundur WhatsApp selesai). */
    fun resend(root: AccessibilityNodeInfo): Boolean {
        val node = findByText(root, RESEND)?.let { clickableAncestor(it) } ?: return false
        if (!node.isEnabled) return false
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        if (ok) acted()
        return ok
    }

    fun reset() {
        filledPhone = null
        lastActionAt = 0L
    }

    private fun fillPhone(root: AccessibilityNodeInfo, pkg: String, national: String, full: String) {
        if (filledPhone == full) return
        val local = national.removePrefix("0")
        val cc = byId(root, pkg, "registration_cc")
        val phone = byId(root, pkg, "registration_phone") ?: return
        if (cc != null && cc.text?.toString() != "62") setText(cc, "62")
        if (!setText(phone, local)) return
        filledPhone = full
        AutomationEngine.phoneFilled = true
        AutomationEngine.addLog("WhatsApp: nomor +62 $local diisi.")
        if (!clickById(root, pkg, "registration_submit")) clickByText(root, NEXT)
        acted()
    }

    private fun fillOtp(root: AccessibilityNodeInfo, pkg: String, otp: String): Boolean {
        byId(root, pkg, "verify_sms_code_input")?.let { return setText(it, otp) }
        val fields = mutableListOf<AccessibilityNodeInfo>()
        collect(root, fields) { it.className?.toString() == "android.widget.EditText" }
        return when {
            fields.size == 1 -> setText(fields[0], otp)
            fields.size == otp.length -> fields.indices.all { setText(fields[it], otp[it].toString()) }
            else -> false
        }
    }

    private fun isConfirmDialog(text: String, national: String): Boolean {
        val digits = national.removePrefix("0").takeLast(6)
        return digits.length == 6 && text.replace(Regex("[\\s-]"), "").contains(digits) &&
            (text.contains("benar") || text.contains("correct") || text.contains("apakah"))
    }

    private fun isMethodChooser(text: String): Boolean =
        (text.contains("verifikasi") || text.contains("verify")) && (text.contains("telepon") ||
            text.contains("call") || text.contains("cara lain") || text.contains("another way")) &&
            text.contains("sms")

    private fun acted() {
        lastActionAt = SystemClock.uptimeMillis()
    }

    // ------------------------------------------------------------------------------------------ utilitas node
    private fun screenText(root: AccessibilityNodeInfo): String {
        val parts = StringBuilder()
        fun walk(node: AccessibilityNodeInfo?) {
            node ?: return
            node.text?.let { parts.append(it).append('\n') }
            node.contentDescription?.let { parts.append(it).append('\n') }
            for (i in 0 until node.childCount) walk(node.getChild(i))
        }
        walk(root)
        return parts.toString().lowercase()
    }

    private fun byId(root: AccessibilityNodeInfo, pkg: String, id: String): AccessibilityNodeInfo? =
        root.findAccessibilityNodeInfosByViewId("$pkg:id/$id").firstOrNull()

    private fun clickById(root: AccessibilityNodeInfo, pkg: String, id: String): Boolean {
        val node = byId(root, pkg, id) ?: return false
        return clickableAncestor(node)?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
    }

    /** Teks persis (tanpa beda huruf besar/kecil) didahulukan agar "ya" tidak mengenai "hanya". */
    private fun findByText(root: AccessibilityNodeInfo, words: List<String>): AccessibilityNodeInfo? {
        val all = mutableListOf<AccessibilityNodeInfo>()
        collect(root, all) { !it.text.isNullOrBlank() || !it.contentDescription.isNullOrBlank() }
        fun label(n: AccessibilityNodeInfo) = (n.text ?: n.contentDescription).toString().trim().lowercase()
        for (w in words) all.firstOrNull { label(it) == w }?.let { return it }
        for (w in words.filter { it.length > 3 }) all.firstOrNull { label(it).contains(w) }?.let { return it }
        return null
    }

    private fun clickByText(root: AccessibilityNodeInfo, words: List<String>): Boolean {
        val node = findByText(root, words)?.let { clickableAncestor(it) } ?: return false
        return node.isEnabled && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    private fun clickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var cur: AccessibilityNodeInfo? = node
        while (cur != null && !cur.isClickable) cur = cur.parent
        return cur
    }

    private fun setText(node: AccessibilityNodeInfo, value: String): Boolean {
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun collect(
        node: AccessibilityNodeInfo?,
        out: MutableList<AccessibilityNodeInfo>,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ) {
        node ?: return
        if (predicate(node)) out.add(node)
        for (i in 0 until node.childCount) collect(node.getChild(i), out, predicate)
    }
}
