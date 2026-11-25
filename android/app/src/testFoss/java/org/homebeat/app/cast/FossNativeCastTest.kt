package org.homebeat.app.cast

import org.junit.Assert.assertFalse
import org.junit.Test

class FossNativeCastTest {
    @Test fun `foss build has no native cast`() {
        assertFalse(NativeCast.AVAILABLE)
    }
}
