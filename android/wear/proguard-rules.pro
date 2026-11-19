# Keep all nas-music code whole: R8 is only here to drop unused library code (mostly the icon set).
-keep class org.nasmusic.** { *; }
-keepattributes *Annotation*, Signature, InnerClasses, EnclosingMethod, Exceptions
-dontwarn org.nasmusic.**
