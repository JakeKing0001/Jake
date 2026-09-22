# Companion Mobile MVP (F7.2): minifyEnabled e' false in questo MVP (vedi app/build.gradle.kts) - questo file
# resta pronto per quando verra' attivato, non usato oggi.

# kotlinx.serialization ha bisogno dei propri generatori di serializzatori a runtime.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class **$$serializer {
    private ** descriptor;
}
-keepclassmembers class * {
    *** Companion;
}
-keepclasseswithmembers class * {
    kotlinx.serialization.KSerializer serializer(...);
}
