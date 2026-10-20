package cloud.ardh1337.scpool.service

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import cloud.ardh1337.scpool.R
import cloud.ardh1337.scpool.data.OrderStatus
import cloud.ardh1337.scpool.ui.MainActivity
import cloud.ardh1337.scpool.util.SoundHelper
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.collectLatest

class FloatingOverlayService : Service() {

    private var windowManager: WindowManager? = null
    private var overlayView: View? = null
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    companion object {
        const val CHANNEL_ID = "scpool_automation_channel"
        const val NOTIF_ID = 1337

        fun start(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(context)) {
                return
            }
            val intent = Intent(context, FloatingOverlayService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            val intent = Intent(context, FloatingOverlayService::class.java)
            context.stopService(intent)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification("SCPool Otomasi Aktif", "Siap memproses nomor & OTP"))

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)) {
            initOverlay()
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.channel_description)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(title: String, text: String): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            intent,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun initOverlay() {
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val inflater = LayoutInflater.from(this)
        overlayView = inflater.inflate(R.layout.overlay_status, null)

        val layoutType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            layoutType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 30
            y = 150
        }

        val tvService = overlayView?.findViewById<TextView>(R.id.tv_overlay_service)
        val tvPhone = overlayView?.findViewById<TextView>(R.id.tv_overlay_phone)
        val tvStatus = overlayView?.findViewById<TextView>(R.id.tv_overlay_status)
        val tvOtp = overlayView?.findViewById<TextView>(R.id.tv_overlay_otp)
        val layoutOtp = overlayView?.findViewById<LinearLayout>(R.id.layout_overlay_otp)
        val btnCopy = overlayView?.findViewById<Button>(R.id.btn_overlay_copy)
        val btnFinish = overlayView?.findViewById<Button>(R.id.btn_overlay_finish)
        val btnClose = overlayView?.findViewById<ImageButton>(R.id.btn_overlay_close)

        btnCopy?.setOnClickListener {
            val order = AutomationEngine.currentOrder.value
            if (order != null) {
                if (!order.otp.isNullOrBlank()) {
                    SoundHelper.copyToClipboard(this, "OTP", order.otp)
                } else {
                    SoundHelper.copyToClipboard(this, "Nomor", order.phoneNational)
                }
            }
        }

        btnFinish?.setOnClickListener {
            AutomationEngine.finishOrder(this)
        }

        btnClose?.setOnClickListener {
            stopSelf()
        }

        // Drag and move gesture
        var initialX = 0
        var initialY = 0
        var touchX = 0f
        var touchY = 0f

        overlayView?.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    touchX = event.rawX
                    touchY = event.rawY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    params.x = initialX + (event.rawX - touchX).toInt()
                    params.y = initialY + (event.rawY - touchY).toInt()
                    windowManager?.updateViewLayout(overlayView, params)
                    true
                }
                else -> false
            }
        }

        windowManager?.addView(overlayView, params)

        // Dengarkan perubahan status dari AutomationEngine
        serviceScope.launch {
            AutomationEngine.currentOrder.collectLatest { order ->
                if (order == null) {
                    tvService?.text = "SCPool"
                    tvPhone?.text = "Belum ada pesanan"
                    tvStatus?.text = "SIAP"
                    layoutOtp?.visibility = View.GONE
                } else {
                    tvService?.text = order.service.uppercase()
                    tvPhone?.text = order.phoneNational
                    tvStatus?.text = order.status.name

                    if (order.status == OrderStatus.OTP_RECEIVED && !order.otp.isNullOrBlank()) {
                        layoutOtp?.visibility = View.VISIBLE
                        tvOtp?.text = order.otp
                        btnCopy?.text = "Salin OTP"
                    } else {
                        layoutOtp?.visibility = View.GONE
                        btnCopy?.text = "Salin No"
                    }
                }
            }
        }
    }

    override fun onDestroy() {
        serviceScope.cancel()
        overlayView?.let {
            windowManager?.removeView(it)
            overlayView = null
        }
        super.onDestroy()
    }
}

