package org.homebeat.app.stability

import org.junit.Assert.assertEquals
import org.junit.Test
import org.homebeat.app.ui.ShellLayout

class LayoutTest {
    @Test fun `phone portrait, phone landscape, tablet`() {
        assertEquals(ShellLayout.PHONE, ShellLayout.of(411, 915)) // moto g75 portrait
        // moto g75 landscape: wide but short -> rail + full-screen Now Playing, never the side pane
        assertEquals(ShellLayout.RAIL, ShellLayout.of(915, 411))
        assertEquals(ShellLayout.RAIL, ShellLayout.of(800, 1280)) // tablet portrait
        assertEquals(ShellLayout.TABLET_SIDE_PANE, ShellLayout.of(1280, 800)) // tablet landscape
        assertEquals(ShellLayout.TABLET_SIDE_PANE, ShellLayout.of(884, 838)) // unfolded foldable
    }
}

class QueueSheetStyleTest {
    @Test fun `queue popup is a bottom sheet in phone portrait, a side panel otherwise`() {
        assertEquals(org.homebeat.app.ui.QueueSheetStyle.BOTTOM, org.homebeat.app.ui.QueueSheetStyle.of(411, 915))
        assertEquals(org.homebeat.app.ui.QueueSheetStyle.SIDE, org.homebeat.app.ui.QueueSheetStyle.of(915, 411)) // phone landscape
        assertEquals(org.homebeat.app.ui.QueueSheetStyle.SIDE, org.homebeat.app.ui.QueueSheetStyle.of(800, 1280)) // tablet portrait
        assertEquals(org.homebeat.app.ui.QueueSheetStyle.SIDE, org.homebeat.app.ui.QueueSheetStyle.of(1280, 800))
    }
}
