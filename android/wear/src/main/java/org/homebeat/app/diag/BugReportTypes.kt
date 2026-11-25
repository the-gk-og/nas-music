package org.homebeat.app.diag

import kotlinx.serialization.Serializable

// The shared HomebeatApi (copied from the phone app) names these types for POST /me/bug-report.
// The phone's diag/BugReport.kt needs the phone's BuildConfig, so the watch carries just the shapes.
// The watch does not send bug reports yet.

@Serializable
data class CrashFile(val name: String, val text: String)

@Serializable
data class BugReportBody(
    val description: String,
    val appVersion: String,
    val versionCode: Int,
    val device: String,
    val androidVersion: String,
    val sdk: Int,
    val screen: String,
    val state: Map<String, String> = emptyMap(),
    val log: String,
    val crashes: List<CrashFile> = emptyList(),
)

@Serializable
data class BugReportResponse(val id: String? = null)
