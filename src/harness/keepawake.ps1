$sig = '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$k = Add-Type -MemberDefinition $sig -Name KeepAwake -Namespace Hunt -PassThru
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no idle sleep while this process lives (the display may still turn off)
$prev = $k::SetThreadExecutionState([uint32]2147483649)
"keepawake ok prev=$prev pid=$PID"
while ($true) { Start-Sleep -Seconds 30; $null = $k::SetThreadExecutionState([uint32]2147483649) }
