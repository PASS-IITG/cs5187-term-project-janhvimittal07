$sig = '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$k = Add-Type -MemberDefinition $sig -Name KeepAwake -Namespace Hunt -PassThru
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED (0x80000003). On Modern Standby laptops (S0 Low Power Idle, no S3)
# standby starts when the screen times out, and ES_SYSTEM_REQUIRED alone does NOT stop that (observed 2026-11-08: standby 3 min
# after start on battery). Keeping the display "required" does; the brightness can be turned down, only the timeout matters.
$prev = $k::SetThreadExecutionState([uint32]2147483651)
"keepawake ok prev=$prev pid=$PID"
while ($true) { Start-Sleep -Seconds 30; $null = $k::SetThreadExecutionState([uint32]2147483651) }
