package org.homebeat.wear.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Person
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text
import kotlinx.coroutines.launch
import org.homebeat.app.net.ProbeOutcome
import org.homebeat.app.net.TrustDecision
import org.homebeat.wear.WearContainer
import org.homebeat.wear.auth.WearAuth
import org.homebeat.wear.ui.ListScreen
import org.homebeat.wear.ui.NavChip
import org.homebeat.wear.ui.header
import org.homebeat.wear.ui.loading
import org.homebeat.wear.ui.message

private sealed interface Step {
    data object Address : Step
    data class Trust(val p: ProbeOutcome, val changed: Boolean) : Step
    data class Method(val p: ProbeOutcome) : Step
    data class Code(val p: ProbeOutcome) : Step
    data class Password(val p: ProbeOutcome) : Step
}

/**
 * Sign-in on the watch. Typing is painful on a watch, so the short path is: on the phone app (Account →
 * Add a phone) or the web player (Account → Pair a phone with a code) get a one-time code, then type
 * the server address and the 8-character code here.
 * Username + password works too.
 */
@Composable
fun SignInScreen(c: WearContainer) {
    val scope = rememberCoroutineScope()
    val saved = c.secureStore.config.value
    var step by remember { mutableStateOf<Step>(Step.Address) }
    var address by remember { mutableStateOf(saved?.tunnelUrl ?: saved?.lanUrl ?: "") }
    var code by remember { mutableStateOf("") }
    var user by remember { mutableStateOf(saved?.username ?: "") }
    var pass by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun run(block: suspend () -> Unit) {
        busy = true; error = null
        scope.launch {
            try { block() } catch (e: Exception) { error = e.message ?: e.javaClass.simpleName } finally { busy = false }
        }
    }

    ListScreen {
        when (val s = step) {
            Step.Address -> {
                header("Sign in to Homebeat")
                message("Server address (home IP or your public address)")
                item { Field(address, { address = it }, "192.168.1.50 or music.example.com", KeyboardType.Uri) }
                error?.let { message(it) }
                if (busy) loading() else item {
                    NavChip("Next", primary = true, enabled = address.isNotBlank()) {
                        run {
                            val (p, d) = c.auth.probe(address)
                            step = when (d) {
                                TrustDecision.CA_TRUSTED, TrustDecision.KNOWN_PIN -> Step.Method(p)
                                TrustDecision.ASK_USER -> Step.Trust(p, changed = false)
                                TrustDecision.PIN_CHANGED -> Step.Trust(p, changed = true)
                            }
                        }
                    }
                }
            }
            is Step.Trust -> {
                header(if (s.changed) "Certificate changed!" else "Trust this server?")
                message(
                    if (s.changed) "The server's certificate is not the one this watch knew. Only continue if you replaced it."
                    else "${s.p.info.name} uses its own certificate. Check that this matches the server dashboard:",
                )
                item {
                    Text(
                        WearAuth.shortFingerprint(s.p.fingerprint),
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.caption3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                item { NavChip("Trust", primary = true) { step = Step.Method(s.p) } }
                item { NavChip("Back") { step = Step.Address } }
            }
            is Step.Method -> {
                header(s.p.info.name)
                message("Get a code: phone app Account → Add a phone, or web player Account → Pair a phone with a code.")
                item { NavChip("Enter pair code", icon = Icons.Filled.Key, primary = true) { error = null; step = Step.Code(s.p) } }
                item { NavChip("Username & password", icon = Icons.Filled.Person) { error = null; step = Step.Password(s.p) } }
                item { NavChip("Back") { step = Step.Address } }
            }
            is Step.Code -> {
                header("Pair code")
                message("8 letters/digits, valid 10 minutes")
                item { Field(code, { code = it }, "ABCD2345", KeyboardType.Ascii, caps = true) }
                error?.let { message(it) }
                if (busy) loading() else {
                    item { NavChip("Pair", primary = true, enabled = WearAuth.normalizeCode(code).length >= 6) { run { c.auth.pair(s.p, code) } } }
                    item { NavChip("Back") { step = Step.Method(s.p) } }
                }
            }
            is Step.Password -> {
                header("Sign in")
                item { Field(user, { user = it }, "Username", KeyboardType.Ascii) }
                item { Field(pass, { pass = it }, "Password", KeyboardType.Password, password = true) }
                error?.let { message(it) }
                if (busy) loading() else {
                    item {
                        NavChip("Sign in", primary = true, enabled = user.isNotBlank() && pass.isNotEmpty()) {
                            run { c.auth.login(s.p, user, pass) }
                        }
                    }
                    item { NavChip("Back") { step = Step.Method(s.p) } }
                }
            }
        }
    }
}

@Composable
private fun Field(
    value: String,
    onChange: (String) -> Unit,
    hint: String,
    type: KeyboardType,
    password: Boolean = false,
    caps: Boolean = false,
) {
    val style = TextStyle(color = Color.White, fontSize = 14.sp, textAlign = TextAlign.Center)
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = true,
        textStyle = style,
        cursorBrush = SolidColor(MaterialTheme.colors.primary),
        visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(
            keyboardType = type,
            imeAction = ImeAction.Done,
            autoCorrectEnabled = false,
            capitalization = if (caps) KeyboardCapitalization.Characters else KeyboardCapitalization.None,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFF202124), RoundedCornerShape(16.dp))
            .border(1.dp, Color(0xFF5F6368), RoundedCornerShape(16.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        decorationBox = { inner ->
            androidx.compose.foundation.layout.Box {
                if (value.isEmpty()) Text(hint, style = style.copy(color = Color(0xFF9AA0A6)), modifier = Modifier.fillMaxWidth())
                inner()
            }
        },
    )
}
