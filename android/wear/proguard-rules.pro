# Keep all Homebeat code whole: R8 is only here to drop unused library code (mostly the icon set).
-keep class org.homebeat.** { *; }
-keepattributes *Annotation*, Signature, InnerClasses, EnclosingMethod, Exceptions
-dontwarn org.homebeat.**
