package org.nasmusic.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.fragment.app.FragmentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import org.nasmusic.app.ui.NasMusicRoot

class MainActivity : FragmentActivity() { // FragmentActivity: the Cast button's picker is a DialogFragment
    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        intent?.getStringExtra(EXTRA_ROUTE)?.let { container.pendingRoute.value = it }
        setContent { NasMusicRoot(container) }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_ROUTE)?.let { container.pendingRoute.value = it }
    }

    companion object {
        /** A screen to open (e.g. from the "new music" notification). */
        const val EXTRA_ROUTE = "org.nasmusic.app.ROUTE"
    }

    override fun onStart() {
        super.onStart()
        container.player.connect()
    }

    override fun onStop() {
        container.player.release()
        super.onStop()
    }
}
