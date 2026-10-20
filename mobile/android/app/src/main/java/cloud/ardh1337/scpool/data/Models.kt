package cloud.ardh1337.scpool.data

enum class OrderStatus {
    IDLE,
    ORDERING,
    ACTIVE,
    OTP_RECEIVED,
    DONE,
    CANCELLED,
    EXPIRED
}

data class ServiceItem(
    val service: String,
    val displayName: String,
    val activeSims: Int
)

data class SmsMessage(
    val id: Int,
    val sender: String,
    val body: String,
    val receivedAt: String
)

data class OrderData(
    val orderId: Int,
    val service: String,
    val phoneNumber: String,
    val phoneNational: String,
    val status: OrderStatus,
    val secondsLeft: Int,
    val otp: String?,
    val sms: SmsMessage?
)

