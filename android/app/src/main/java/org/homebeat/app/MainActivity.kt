package org.homebeat.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.fragment.app.FragmentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import org.homebeat.app.ui.HomebeatRoot

class MainActivity : FragmentActivity() { // FragmentActivity: the Cast button's picker is a DialogFragment
    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        intent?.getStringExtra(EXTRA_ROUTE)?.let { container.pendingRoute.value = it }
        setContent { HomebeatRoot(container) }
        org.homebeat.app.ui.AppShortcuts.publish(this)
        if (savedInstanceState == null) org.homebeat.app.ui.LaunchRequests.handle(intent)
    }

    /** A widget/quick-settings tile (Now Playing or an item) or a notification (a route) while the app was open. */
    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        org.homebeat.app.ui.LaunchRequests.handle(intent)
        intent.getStringExtra(EXTRA_ROUTE)?.let { container.pendingRoute.value = it }
    }

    companion object {
        /** A screen to open (e.g. from the "new music" notification). */
        const val EXTRA_ROUTE = "org.homebeat.app.ROUTE"
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
