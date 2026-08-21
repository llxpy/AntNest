# AntNest ProGuard Rules
-keepattributes Signature
-keepattributes *Annotation*

# Gson
-keep class com.antnest.app.data.model.** { *; }
-keep class com.google.gson.** { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Compose
-dontwarn androidx.compose.**
