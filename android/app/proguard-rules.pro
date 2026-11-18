# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers @kotlinx.serialization.Serializable class org.nasmusic.app.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
# Retrofit interfaces
-keep,allowobfuscation interface org.nasmusic.app.data.api.NasMusicApi
-keepattributes Signature, Exceptions
# Tink
-dontwarn com.google.errorprone.annotations.**
