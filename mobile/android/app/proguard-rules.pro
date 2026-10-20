# Proguard rules for SCPool Automation
-keepattributes *Annotation*
-keepclassmembers class * {
    @org.json.* <fields>;
}

