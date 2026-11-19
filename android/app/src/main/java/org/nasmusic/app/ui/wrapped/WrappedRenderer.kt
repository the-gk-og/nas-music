package org.nasmusic.app.ui.wrapped

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface

/** Draws one Wrapped card onto a 1080×1920 bitmap (9:16 story format). Plain android.graphics, no Compose. */
object WrappedRenderer {
    const val W = 1080
    const val H = 1920

    fun render(card: WrappedCard, colors: Pair<String, String>, covers: Map<String, Bitmap>, footer: String): Bitmap {
        val bmp = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888)
        draw(Canvas(bmp), card, colors, covers, footer)
        return bmp
    }

    private fun color(hex: String, fallback: Int) = runCatching { Color.parseColor(hex) }.getOrDefault(fallback)

    private fun paint(px: Float, bold: Boolean = true, alpha: Int = 255) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        this.alpha = alpha
        textSize = px
        typeface = Typeface.create(Typeface.DEFAULT, if (bold) Typeface.BOLD else Typeface.NORMAL)
    }

    private fun fit(p: Paint, text: String, max: Float): String {
        if (p.measureText(text) <= max) return text
        var t = text
        while (t.length > 1 && p.measureText("$t…") > max) t = t.dropLast(1)
        return "$t…"
    }

    private fun wrap(c: Canvas, p: Paint, text: String, x: Float, y0: Float, max: Float, lh: Float) {
        var y = y0
        var line = ""
        for (word in text.split(' ')) {
            val t = if (line.isEmpty()) word else "$line $word"
            if (p.measureText(t) > max && line.isNotEmpty()) { c.drawText(line, x, y, p); y += lh; line = word } else line = t
        }
        if (line.isNotEmpty()) c.drawText(line, x, y, p)
    }

    private fun cover(c: Canvas, bmp: Bitmap?, l: Float, t: Float, size: Float) {
        bmp ?: return
        c.drawBitmap(bmp, Rect(0, 0, bmp.width, bmp.height), RectF(l, t, l + size, t + size), Paint(Paint.FILTER_BITMAP_FLAG))
    }

    fun draw(c: Canvas, card: WrappedCard, colors: Pair<String, String>, covers: Map<String, Bitmap>, footer: String) {
        val w = W.toFloat()
        val bg = Paint().apply {
            shader = LinearGradient(0f, 0f, w, H.toFloat(), color(colors.first, Color.GREEN), color(colors.second, Color.MAGENTA), Shader.TileMode.CLAMP)
        }
        c.drawRect(0f, 0f, w, H.toFloat(), bg)
        c.drawColor(Color.argb(90, 0, 0, 0))
        c.drawText(fit(paint(64f), card.title, w - 160), 80f, 200f, paint(64f))
        when (card) {
            is WrappedCard.Intro -> big(c, card.big, card.caption, null)
            is WrappedCard.Stat -> big(c, card.big, card.caption, card.sub)
            is WrappedCard.ListCard -> {
                val hero = card.heroAlbumId?.let { covers[it] }
                cover(c, hero, 80f, 280f, 560f)
                var y = if (hero != null) 960f else 360f
                card.rows.forEachIndexed { i, r ->
                    c.drawText("${i + 1}", 80f, y + 50, paint(56f))
                    val th = r.albumId?.let { covers[it] }
                    val x = if (th != null) 300f else 170f
                    cover(c, th, 160f, y - 20, 110f)
                    val t = paint(48f)
                    c.drawText(fit(t, r.title, w - x - 260), x, y + 30, t)
                    val s = paint(36f, bold = false, alpha = 190)
                    r.sub?.let { c.drawText(fit(s, it, w - x - 260), x, y + 80, s) }
                    r.value?.let { c.drawText(it, w - 80 - s.measureText(it), y + 30, s) }
                    y += 170
                }
            }
            is WrappedCard.Genres -> {
                var y = 420f
                val max = card.genres.maxOf { it.second }.coerceAtLeast(0.01)
                card.genres.forEach { (name, share) ->
                    val p = paint(52f)
                    c.drawText(fit(p, name, w - 360), 80f, y, p)
                    val pct = "${Math.round(share * 100)}%"
                    c.drawText(pct, w - 80 - p.measureText(pct), y, p)
                    c.drawRect(80f, y + 30, w - 80, y + 58, Paint().apply { color = Color.argb(64, 255, 255, 255) })
                    c.drawRect(80f, y + 30, 80f + ((w - 160) * (share / max)).toFloat(), y + 58, Paint().apply { color = Color.WHITE })
                    y += 220
                }
            }
            is WrappedCard.Clock -> {
                val max = (card.byHour.maxOrNull() ?: 0.0).coerceAtLeast(0.01)
                val bw = (w - 160) / 24
                val base = 1300f
                card.byHour.forEachIndexed { h, v ->
                    val bh = (v / max * 700).toFloat()
                    c.drawRect(80 + h * bw + 4, base - bh, 80 + (h + 1) * bw - 4, base, Paint().apply { color = Color.WHITE; alpha = if (v == max) 255 else 150 })
                }
                val small = paint(32f, bold = false, alpha = 190)
                listOf(0, 6, 12, 18).forEach { c.drawText(hourLabel(it), 80 + it * bw, base + 50, small) }
                wrap(c, paint(52f), card.caption, 80f, 1500f, w - 160, 70f)
            }
            is WrappedCard.Aura -> {
                val a = card.aura
                val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    shader = RadialGradient(w / 2, 760f, 420f, color(a.colors.firstOrNull() ?: "#ffffff", Color.WHITE), Color.TRANSPARENT, Shader.TileMode.CLAMP)
                }
                c.drawCircle(w / 2, 760f, 420f, glow)
                val label = paint(150f).apply { textAlign = Paint.Align.CENTER }
                c.drawText(a.label, w / 2, 1350f, label)
                wrap(c, paint(48f, bold = false).apply { textAlign = Paint.Align.CENTER }, a.description, w / 2, 1450f, w - 200, 64f)
                val moods = a.moods.take(3).joinToString(" · ") { it.name }
                if (moods.isNotEmpty()) c.drawText(moods, w / 2, 1650f, paint(40f).apply { textAlign = Paint.Align.CENTER })
            }
            is WrappedCard.Summary -> card.cells.forEachIndexed { i, (label, value) ->
                val x = 80f + (i % 2) * 470
                val y = 420f + (i / 2) * 330
                c.drawText(label, x, y, paint(40f, bold = false, alpha = 190))
                val p = paint(if (value.length > 9) 56f else 90f)
                c.drawText(fit(p, value, 420f), x, y + 100, p)
            }
        }
        c.drawText(footer, 80f, H - 80f, paint(36f, alpha = 180))
    }

    private fun big(c: Canvas, big: String, caption: String, sub: String?) {
        val w = W.toFloat()
        val p = paint(if (big.length > 6) 180f else 260f)
        c.drawText(fit(p, big, w - 160), 80f, 900f, p)
        val cp = paint(56f, bold = false)
        c.drawText(fit(cp, caption, w - 160), 80f, 1000f, cp)
        sub?.let { val s = paint(48f, bold = false, alpha = 205); c.drawText(fit(s, it, w - 160), 80f, 1100f, s) }
    }
}
