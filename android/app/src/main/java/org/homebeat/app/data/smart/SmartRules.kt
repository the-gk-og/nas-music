package org.homebeat.app.data.smart

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.homebeat.app.data.api.SmartDefinition
import org.homebeat.app.data.api.SmartFieldSpec
import org.homebeat.app.data.api.SmartNode
import java.time.LocalDate
import kotlin.math.roundToInt

/** Smart playlist editor helpers (api 0.7). The server validates and evaluates; these keep rows well-formed. */
object SmartRules {
    val OP_LABELS = mapOf(
        "eq" to "is", "ne" to "is not", "gt" to "is more than", "gte" to "is at least", "lt" to "is less than",
        "lte" to "is at most", "between" to "is between", "is" to "is", "isNot" to "is not", "contains" to "contains",
        "notContains" to "doesn't contain", "startsWith" to "starts with", "inLast" to "in the last",
        "notInLast" to "not in the last", "before" to "before", "after" to "after", "never" to "never", "ever" to "ever",
    )

    fun num(v: Double): JsonPrimitive = if (v == Math.floor(v) && !v.isInfinite()) JsonPrimitive(v.toLong()) else JsonPrimitive(v)

    fun defaultValue(f: SmartFieldSpec, op: String): JsonElement = when (f.type) {
        "number" -> if (op == "between") when (f.id) {
            "year" -> JsonArray(listOf(num(1990.0), num(1999.0)))
            "tempo" -> JsonArray(listOf(num(100.0), num(130.0)))
            else -> JsonArray(listOf(num(1.0), num(10.0)))
        } else num(when (f.id) { "year" -> 2000.0; "rating" -> 4.0; "tempo" -> 120.0; "durationS" -> 300.0; else -> 1.0 })
        "percent" -> if (op == "between") JsonArray(listOf(JsonPrimitive(0.3), JsonPrimitive(0.7))) else JsonPrimitive(0.5)
        "text", "playlist" -> JsonPrimitive("")
        "date" -> when (op) {
            "inLast", "notInLast" -> num(1.0)
            "before", "after" -> JsonPrimitive(LocalDate.now().toString())
            else -> JsonNull
        }
        "bool" -> JsonPrimitive(true)
        "enum" -> JsonPrimitive(f.options.firstOrNull() ?: "")
        else -> JsonNull
    }

    fun newRule(f: SmartFieldSpec, op: String = f.ops.first()): SmartNode =
        SmartNode(field = f.id, op = op, value = defaultValue(f, op), unit = if (f.type == "date" && op in setOf("inLast", "notInLast")) "months" else null)

    /** Change a rule's field or operator, keeping the value when it still fits. */
    fun retarget(r: SmartNode, f: SmartFieldSpec, op: String? = null): SmartNode {
        val nextOp = when {
            op != null && op in f.ops -> op
            r.field == f.id && r.op in f.ops -> r.op!!
            else -> f.ops.first()
        }
        val keep = r.field == f.id && (nextOp == "between") == (r.op == "between") &&
            !(f.type == "date" && dateKind(nextOp) != dateKind(r.op))
        val unit = if (f.type == "date" && nextOp in setOf("inLast", "notInLast")) r.unit ?: "months" else null
        return SmartNode(field = f.id, op = nextOp, value = if (keep) r.value else defaultValue(f, nextOp), unit = unit)
    }

    private fun dateKind(op: String?) = when (op) { "inLast", "notInLast" -> 0; "before", "after" -> 1; else -> 2 }

    fun incomplete(rules: List<SmartNode>, fields: List<SmartFieldSpec>): Boolean = rules.any { r ->
        if (r.isGroup) return@any incomplete(r.rules.orEmpty(), fields)
        val f = fields.firstOrNull { it.id == r.field } ?: return@any true
        when (f.type) {
            "text", "playlist" -> r.text.isNullOrBlank()
            "number", "percent" -> if (r.op == "between") r.pair?.let { p -> p.size != 2 || p.any { it.isNaN() } } ?: true else r.num?.isNaN() ?: true
            "date" -> when (r.op) { "inLast", "notInLast" -> (r.num ?: 0.0) <= 0; "before", "after" -> r.text.isNullOrBlank(); else -> false }
            else -> false
        }
    }

    private fun fmt(f: SmartFieldSpec?, v: Double) = if (f?.type == "percent") "${(v * 100).roundToInt()}%" else if (v == Math.floor(v)) v.toLong().toString() else v.toString()

    fun describe(r: SmartNode, fields: List<SmartFieldSpec>): String {
        val f = fields.firstOrNull { it.id == r.field }
        val label = f?.label ?: r.field.orEmpty()
        val op = OP_LABELS[r.op] ?: r.op.orEmpty()
        return when {
            r.op == "never" || r.op == "ever" -> "$label: $op"
            r.op == "between" -> r.pair?.let { "$label $op ${fmt(f, it[0])} and ${fmt(f, it[1])}" } ?: label
            r.op == "inLast" || r.op == "notInLast" -> {
                val n = (r.num ?: 0.0).toLong()
                "$label $op $n ${(r.unit ?: "days").removeSuffix("s")}${if (n == 1L) "" else "s"}"
            }
            f?.type == "bool" -> "$label ${if (r.op == "is") "is" else "is not"} ${if (r.bool == true) "yes" else "no"}"
            f?.type == "text" -> "$label $op \"${r.text}\""
            r.num != null -> "$label $op ${fmt(f, r.num!!)}"
            else -> "$label $op ${r.text.orEmpty()}"
        }
    }

    fun describe(d: SmartDefinition, fields: List<SmartFieldSpec>): String {
        fun join(rules: List<SmartNode>, match: String?): String = rules.joinToString(if (match == "any") " or " else " and ") {
            if (it.isGroup) "(${join(it.rules.orEmpty(), it.match)})" else describe(it, fields)
        }
        return if (d.rules.isEmpty()) "All songs" else join(d.rules, d.match)
    }

    fun duration(s: Double): String {
        val m = (s / 60).roundToInt()
        return if (m < 60) "$m min" else "${m / 60} h ${m % 60} min"
    }
}
