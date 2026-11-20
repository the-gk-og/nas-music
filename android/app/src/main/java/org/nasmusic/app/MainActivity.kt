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
        setContent { NasMusicRoot(container) }
        if (savedInstanceState == null) org.nasmusic.app.ui.LaunchRequests.handle(intent)
    }

    /** A widget or quick-settings tile asked for Now Playing (or an item) while the app was open. */
    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        org.nasmusic.app.ui.LaunchRequests.handle(intent)
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
