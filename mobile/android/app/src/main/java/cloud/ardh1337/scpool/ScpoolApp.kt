package cloud.ardh1337.scpool

import android.app.Application
import cloud.ardh1337.scpool.util.Prefs

class ScpoolApp : Application() {

    lateinit var prefs: Prefs
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = Prefs(this)
    }

    companion object {
        lateinit var instance: ScpoolApp
            private set
    }
}

