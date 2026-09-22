package com.jake.companion.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Tema minimale (F7.2: "evita... UI complessa") - Material3 di default, nessuna personalizzazione oltre i
 * colori base, coerente con un primo MVP che deve funzionare prima di essere bello. */
private val JakeDarkColors = darkColorScheme(
    primary = Color(0xFF8AB4F8),
    secondary = Color(0xFFA0A0A0),
    error = Color(0xFFF28B82),
)

private val JakeLightColors = lightColorScheme(
    primary = Color(0xFF1A73E8),
    secondary = Color(0xFF5F6368),
    error = Color(0xFFD93025),
)

@Composable
fun JakeCompanionTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (darkTheme) JakeDarkColors else JakeLightColors
    MaterialTheme(colorScheme = colors, content = content)
}
