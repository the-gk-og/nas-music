# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers @kotlinx.serialization.Serializable class org.homebeat.app.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
# Retrofit interfaces
-keep,allowobfuscation interface org.homebeat.app.data.api.HomebeatApi
-keepattributes Signature, Exceptions
# Tink
-dontwarn com.google.errorprone.annotations.**
