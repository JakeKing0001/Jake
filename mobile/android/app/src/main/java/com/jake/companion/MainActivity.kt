package com.jake.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.lifecycleScope
import com.jake.companion.repository.JakeRepository
import com.jake.companion.storage.SecureDeviceStore
import com.jake.companion.ui.JakeApp

/**
 * Un'unica Activity (F7.2: "evita... UI complessa"): [JakeRepository] vive per tutta la sua durata, connesso
 * mentre l'app e' in primo piano - nessun servizio in background in questo MVP (dichiarato fuori scopo,
 * ROADMAP_EXECUTION.md).
 */
class MainActivity : ComponentActivity() {
    private lateinit var repository: JakeRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val secureStore = SecureDeviceStore(applicationContext)
        repository = JakeRepository(secureStore, lifecycleScope)
        setContent { JakeApp(repository) }
    }

    override fun onStart() {
        super.onStart()
        repository.start()
    }

    override fun onStop() {
        repository.stop()
        super.onStop()
    }
}
