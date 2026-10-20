package cloud.ardh1337.scpool.api

import cloud.ardh1337.scpool.data.OrderData
import cloud.ardh1337.scpool.data.OrderStatus
import cloud.ardh1337.scpool.data.ServiceItem
import cloud.ardh1337.scpool.data.SmsMessage
import cloud.ardh1337.scpool.util.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Server membalas error. 4xx = mengulang tidak akan berhasil (mis. pesanan tidak ada). */
class ApiException(val code: Int, message: String) : IOException(message)

class ScpoolApiClient(private val prefs: Prefs) {

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    // Timeout 70 detik karena long-poll server ?wait=30 dapat menahan koneksi hingga 60 detik
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(70, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private fun buildRequest(method: String, path: String, body: JSONObject? = null): Request {
        val url = prefs.serverUrl + path
        val builder = Request.Builder()
            .url(url)
            .header("X-API-Key", prefs.apiKey)
            .header("Accept", "application/json")

        val reqBody = body?.toString()?.toRequestBody(jsonMedia)
        builder.method(method, reqBody)
        return builder.build()
    }

    private fun parseOrder(obj: JSONObject): OrderData {
        val statusStr = obj.optString("status", "ACTIVE")
        val status = try {
            OrderStatus.valueOf(statusStr)
        } catch (_: Exception) {
            OrderStatus.ACTIVE
        }

        val smsObj = obj.optJSONObject("sms")
        val sms = if (smsObj != null) {
            SmsMessage(
                id = smsObj.optInt("id", 0),
                sender = smsObj.optString("sender", ""),
                body = smsObj.optString("body", ""),
                receivedAt = smsObj.optString("received_at", "")
            )
        } else null

        return OrderData(
            orderId = obj.optInt("order_id", 0),
            service = obj.optString("service", ""),
            phoneNumber = obj.optString("phone_number", ""),
            phoneNational = obj.optString("phone_national", ""),
            status = status,
            secondsLeft = obj.optInt("seconds_left", 0),
            otp = if (obj.isNull("otp")) null else obj.optString("otp"),
            sms = sms
        )
    }

    suspend fun getServices(): List<ServiceItem> = withContext(Dispatchers.IO) {
        val req = buildRequest("GET", "/api/otp/services")
        client.newCall(req).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val detail = try { JSONObject(raw).optString("detail", raw) } catch (_: Exception) { raw }
                throw ApiException(res.code, "Gagal memuat layanan: HTTP ${res.code} - $detail")
            }
            val arr = JSONArray(raw)
            val list = mutableListOf<ServiceItem>()
            for (i in 0 until arr.length()) {
                val item = arr.getJSONObject(i)
                list.add(
                    ServiceItem(
                        service = item.optString("service"),
                        displayName = item.optString("display_name").ifBlank { item.optString("service") },
                        activeSims = item.optInt("active_sims", 0)
                    )
                )
            }
            list
        }
    }

    suspend fun createOrder(service: String, device: String, minutes: Int = 10): OrderData = withContext(Dispatchers.IO) {
        val payload = JSONObject().apply {
            put("service", service)
            put("device", device)
            put("minutes", minutes)
        }
        val req = buildRequest("POST", "/api/otp/orders", payload)
        client.newCall(req).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val detail = try { JSONObject(raw).optString("detail", raw) } catch (_: Exception) { raw }
                throw ApiException(res.code, "Gagal memesan nomor: HTTP ${res.code} - $detail")
            }
            parseOrder(JSONObject(raw))
        }
    }

    suspend fun getOrder(orderId: Int, wait: Int = 30, afterSmsId: Int? = null): OrderData = withContext(Dispatchers.IO) {
        var query = "?wait=$wait"
        if (afterSmsId != null && afterSmsId > 0) {
            query += "&after=$afterSmsId"
        }
        val req = buildRequest("GET", "/api/otp/orders/$orderId$query")
        client.newCall(req).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val detail = try { JSONObject(raw).optString("detail", raw) } catch (_: Exception) { raw }
                throw ApiException(res.code, "Gagal cek pesanan: HTTP ${res.code} - $detail")
            }
            parseOrder(JSONObject(raw))
        }
    }

    /** `result` "success"/"failed" dan `reason` (no_sms, banned, limited, wrong_code, other) bersifat opsional. */
    suspend fun finishOrder(orderId: Int, result: String? = null, reason: String? = null): OrderData =
        withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            if (result != null) put("result", result)
            if (reason != null) put("reason", reason)
        }
        val req = buildRequest("POST", "/api/otp/orders/$orderId/finish", body)
        client.newCall(req).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val detail = try { JSONObject(raw).optString("detail", raw) } catch (_: Exception) { raw }
                throw ApiException(res.code, "Gagal menyelesaikan: HTTP ${res.code} - $detail")
            }
            parseOrder(JSONObject(raw))
        }
    }

    /** Tanpa `reason` = sekadar batal; dengan `reason` = nomor gagal dan tidak diberikan lagi sesuai aturan server. */
    suspend fun cancelOrder(orderId: Int, reason: String? = null): OrderData = withContext(Dispatchers.IO) {
        val body = JSONObject().apply { if (reason != null) put("reason", reason) }
        val req = buildRequest("POST", "/api/otp/orders/$orderId/cancel", body)
        client.newCall(req).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val detail = try { JSONObject(raw).optString("detail", raw) } catch (_: Exception) { raw }
                throw ApiException(res.code, "Gagal membatalkan: HTTP ${res.code} - $detail")
            }
            parseOrder(JSONObject(raw))
        }
    }
}

