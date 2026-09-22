// Companion Mobile MVP (F7.2) - progetto Gradle radice. Nessuna build logic qui: solo la dichiarazione dei
// plugin (risolti alle versioni sotto per tutti i moduli, vedi app/build.gradle.kts).
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "1.9.24" apply false
}
