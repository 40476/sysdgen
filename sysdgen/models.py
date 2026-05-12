from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import re


SECTION_DESCRIPTIONS: Dict[str, str] = {
    "Unit": "Generic unit metadata and dependencies",
    "Service": "Service process configuration",
    "Install": "Installation and enablement configuration",
    "Socket": "Socket activation configuration",
    "Timer": "Timer-based activation configuration",
    "Path": "Path-based activation configuration",
    "Mount": "Mount point configuration",
    "Automount": "Automount point configuration",
    "Swap": "Swap device configuration",
    "Device": "Device unit configuration",
    "Target": "Target unit (grouping only)",
    "Scope": "Scope unit (externally managed processes)",
    "Slice": "Slice unit (resource management grouping)",
}


class SystemdOption:
    """Descriptor for a single systemd configuration option."""
    def __init__(self, key, label, section, option_type="string", description="",
                 default=None, enum=None, multiline=False, placeholder="",
                 advanced=False, deprecated=False, requires=None, conflicts=None):
        self.key = key
        self.label = label
        self.section = section
        self.option_type = option_type
        self.description = description
        self.default = default
        self.enum = enum
        self.multiline = multiline
        self.placeholder = placeholder
        self.advanced = advanced
        self.deprecated = deprecated
        self.requires = requires or []
        self.conflicts = conflicts or []

    def to_ini(self, value):
        if value is None or value == "" or value == []:
            return ""
        if self.multiline and isinstance(value, list):
            if self.option_type == "boolean" and value:
                return f"{self.key}=1\n"
            lines = [f"{self.key}={v}" if i == 0 else f"  {v}" for i, v in enumerate(value)]
            return "\n".join(lines) + "\n"
        if self.option_type == "boolean":
            return f"{self.key}={1 if value else 0}\n"
        if self.option_type == "duration":
            return f"{self.key}={_format_duration(value)}\n"
        if isinstance(value, list):
            if self.option_type == "size":
                return f"{self.key}={_format_size_value(value)}\n"
            return f"{self.key}={' '.join(str(v) for v in value)}\n"
        return f"{self.key}={value}\n"

    def from_ini(self, raw):
        raw = raw.strip().strip('"').strip("'")
        if self.option_type == "boolean":
            return raw.lower() in ("1", "yes", "true", "on")
        if self.option_type == "integer":
            try:
                return int(raw)
            except ValueError:
                return 0
        if self.option_type == "unsigned":
            try:
                return max(0, int(raw))
            except ValueError:
                return 0
        if self.option_type == "duration":
            return _parse_duration(raw)
        if self.option_type == "size":
            return _parse_size(raw)
        if self.option_type == "percent":
            try:
                return int(raw.rstrip("%"))
            except ValueError:
                return 0
        if self.multiline:
            return [v.strip() for v in raw.split("\n") if v.strip()]
        return raw


def _format_duration(seconds):
    if seconds <= 0:
        return "0"
    parts = []
    for unit, label in [(86400, "d"), (3600, "h"), (60, "m"), (1, "s")]:
        if seconds >= unit:
            count = seconds // unit
            parts.append(f"{count}{label}")
            seconds %= unit
    return "".join(parts) if parts else "0"


def _parse_duration(text):
    total = 0
    for m in re.finditer(r"(\d+)\s*([smhdwMy]?)", text):
        val = int(m.group(1))
        unit = m.group(2) or "s"
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 2592000, "y": 31536000}
        total += val * multipliers.get(unit, 1)
    return total


def _format_size_value(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}{value[1]}"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _parse_size(text):
    m = re.match(r"(\d+)\s*([KMGTPE]?)", text)
    if m:
        return [int(m.group(1)), m.group(2) or ""]
    return [0, ""]


@dataclass
class SectionDef:
    name: str
    description: str
    options: Dict[str, SystemdOption] = field(default_factory=dict)

    def add(self, opt):
        self.options[opt.key] = opt

    def to_ini(self, values):
        lines = [f"[{self.name}]"]
        for key, opt in self.options.items():
            val = values.get(key, opt.default)
            line = opt.to_ini(val)
            if line:
                lines.append(line.rstrip("\n"))
        return "\n".join(lines) + "\n\n"


BOOLEAN = "boolean"
STRING = "string"
INTEGER = "integer"
UNSIGNED = "unsigned"
DURATION = "duration"
SIZE = "size"
PERCENT = "percent"


def _opt(key, label, section="Unit", **kw):
    return SystemdOption(key=key, label=label, section=section, **kw)


def _build_sections():
    sections = {}

    unit = SectionDef("Unit", SECTION_DESCRIPTIONS["Unit"])
    for o in [
        _opt("Description", "Description", option_type=STRING, description="Human-readable description of the unit"),
        _opt("Documentation", "Documentation URLs", option_type=STRING, description="Space-separated list of URIs", multiline=True),
        _opt("Wants", "Weak dependencies", option_type=STRING, description="Units started when this unit is", multiline=True),
        _opt("Requires", "Required dependencies", option_type=STRING, description="Units that must succeed with this unit", multiline=True),
        _opt("Requisite", "Requisite dependencies", option_type=STRING, description="Like Requires but fails if not already active", multiline=True),
        _opt("BindsTo", "Binds to units", option_type=STRING, description="Units bound to lifecycle", multiline=True),
        _opt("PartOf", "Part of units", option_type=STRING, description="Stop/restart with listed units", multiline=True),
        _opt("Conflicts", "Conflicting units", option_type=STRING, description="Negative dependency", multiline=True),
        _opt("Before", "Order before", option_type=STRING, description="Start before listed units", multiline=True),
        _opt("After", "Order after", option_type=STRING, description="Start after listed units", multiline=True),
        _opt("OnFailure", "Failure dependency", option_type=STRING, description="Units to activate on failure", multiline=True),
        _opt("OnSuccess", "Success dependency", option_type=STRING, description="Units to activate on success", multiline=True),
        _opt("PropagatesReloadTo", "Propagate reload to", option_type=STRING, description="Send SIGHUP on reload", multiline=True),
        _opt("ReloadPropagatedFrom", "Reload propagated from", option_type=STRING, multiline=True),
        _opt("JoinsNamespaceOf", "Join namespace of", option_type=STRING, multiline=True),
        _opt("RequiresMountsFor", "Requires mounts for", option_type=STRING, multiline=True),
        _opt("IgnoreOnIsolate", "Ignore on isolate", option_type=BOOLEAN, default=True),
        _opt("StopWhenUnneeded", "Stop when unneeded", option_type=BOOLEAN),
        _opt("RefuseManualStart", "Refuse manual start", option_type=BOOLEAN),
        _opt("RefuseManualStop", "Refuse manual stop", option_type=BOOLEAN),
        _opt("AllowIsolate", "Allow isolate", option_type=BOOLEAN, default=True),
        _opt("DefaultDependencies", "Default dependencies", option_type=BOOLEAN, default=True),
        _opt("OnFailureJobMode", "On-failure job mode", option_type=STRING, enum=["fail", "replace", "replace-irreversibly", "flush", "ignore-requirements"], default="replace"),
        _opt("CollectMode", "Collect mode", option_type=STRING, enum=["inactive", "inactive-or-failed"]),
        _opt("ConditionPathExists", "Condition: path exists", option_type=STRING, multiline=True),
        _opt("ConditionPathExistsGlob", "Condition: path exists (glob)", option_type=STRING, multiline=True),
        _opt("ConditionPathIsDirectory", "Condition: path is dir", option_type=BOOLEAN),
        _opt("ConditionPathIsSymbolicLink", "Condition: path is symlink", option_type=BOOLEAN),
        _opt("ConditionPathIsMountPoint", "Condition: path is mount", option_type=BOOLEAN),
        _opt("ConditionPathIsReadWrite", "Condition: path rw", option_type=BOOLEAN),
        _opt("ConditionDirectoryNotEmpty", "Condition: dir not empty", option_type=BOOLEAN),
        _opt("ConditionFileNotEmpty", "Condition: file not empty", option_type=BOOLEAN),
        _opt("ConditionFileIsExecutable", "Condition: file executable", option_type=BOOLEAN),
        _opt("ConditionNeedsUpdate", "Condition: needs update", option_type=STRING, multiline=True),
        _opt("ConditionFirstBoot", "Condition: first boot", option_type=BOOLEAN),
        _opt("ConditionKernelCommandLine", "Condition: kernel cmdline", option_type=STRING, multiline=True),
        _opt("ConditionArchitecture", "Condition: architecture", option_type=STRING, enum=["x86-64", "x86", "arm", "arm64", "ia64", "mips", "parisc", "ppc", "ppc64", "s390", "s390x", "sparc"]),
        _opt("ConditionVirtualization", "Condition: virtualization", option_type=STRING, multiline=True),
        _opt("ConditionSecurity", "Condition: security", option_type=STRING, multiline=True),
        _opt("ConditionCapability", "Condition: capability", option_type=STRING, multiline=True),
        _opt("ConditionHost", "Condition: hostname", option_type=STRING, multiline=True),
        _opt("ConditionACPower", "Condition: AC power", option_type=BOOLEAN),
        _opt("ConditionUser", "Condition: user", option_type=STRING, multiline=True),
        _opt("ConditionGroup", "Condition: group", option_type=STRING, multiline=True),
        _opt("ConditionControlGroupController", "Condition: cgroup", option_type=STRING),
        _opt("ConditionMemory", "Condition: memory", option_type=SIZE),
        _opt("ConditionCPUs", "Condition: CPUs", option_type=UNSIGNED),
        _opt("AssertPathExists", "Assert: path exists", option_type=STRING, multiline=True),
        _opt("AssertPathExistsGlob", "Assert: path exists (glob)", option_type=STRING, multiline=True),
        _opt("AssertPathIsDirectory", "Assert: path is dir", option_type=BOOLEAN),
        _opt("AssertPathIsSymbolicLink", "Assert: path is symlink", option_type=BOOLEAN),
        _opt("AssertPathIsMountPoint", "Assert: path mount", option_type=BOOLEAN),
        _opt("AssertPathIsReadWrite", "Assert: path rw", option_type=BOOLEAN),
        _opt("AssertDirectoryNotEmpty", "Assert: dir not empty", option_type=BOOLEAN),
        _opt("AssertFileNotEmpty", "Assert: file not empty", option_type=BOOLEAN),
        _opt("AssertFileIsExecutable", "Assert: file executable", option_type=BOOLEAN),
        _opt("AssertNeedsUpdate", "Assert: needs update", option_type=STRING, multiline=True),
        _opt("AssertFirstBoot", "Assert: first boot", option_type=BOOLEAN),
        _opt("AssertKernelCommandLine", "Assert: kernel cmdline", option_type=STRING, multiline=True),
        _opt("AssertArchitecture", "Assert: architecture", option_type=STRING, enum=["x86-64", "x86", "arm", "arm64", "ia64", "mips", "parisc", "ppc", "ppc64", "s390", "s390x", "sparc"]),
        _opt("AssertVirtualization", "Assert: virtualization", option_type=STRING, multiline=True),
        _opt("AssertSecurity", "Assert: security", option_type=STRING, multiline=True),
        _opt("AssertCapability", "Assert: capability", option_type=STRING, multiline=True),
        _opt("AssertHost", "Assert: hostname", option_type=STRING, multiline=True),
        _opt("AssertACPower", "Assert: AC power", option_type=BOOLEAN),
        _opt("AssertUser", "Assert: user", option_type=STRING, multiline=True),
        _opt("AssertGroup", "Assert: group", option_type=STRING, multiline=True),
        _opt("AssertControlGroupController", "Assert: cgroup ctlr", option_type=STRING),
        _opt("AssertMemory", "Assert: memory", option_type=SIZE),
        _opt("AssertCPUs", "Assert: CPUs", option_type=UNSIGNED),
        _opt("SourcePath", "Source path", option_type=STRING),
        _opt("Transparent", "Transparent", option_type=BOOLEAN),
    ]:
        unit.add(o)
    sections["Unit"] = unit

    service = SectionDef("Service", SECTION_DESCRIPTIONS["Service"])

    for o in [
        _opt("Type", "Service type", section="Service", option_type=STRING,
             enum=["simple", "exec", "forking", "oneshot", "dbus", "notify", "notify-reload", "idle"],
             default="simple"),
        _opt("RemainAfterExit", "Remain after exit", section="Service", option_type=BOOLEAN),
        _opt("GuessMainPID", "Guess main PID", section="Service", option_type=BOOLEAN, default=True),
        _opt("PIDFile", "PID file path", section="Service", option_type=STRING),
        _opt("BusName", "D-Bus name", section="Service", option_type=STRING),
        _opt("BusPolicy", "D-Bus policy", section="Service", option_type=STRING, enum=["name", "match"]),
        _opt("ExecStart", "ExecStart command", section="Service", option_type=STRING, multiline=True),
        _opt("ExecStartPre", "ExecStartPre", section="Service", option_type=STRING, multiline=True),
        _opt("ExecStartPost", "ExecStartPost", section="Service", option_type=STRING, multiline=True),
        _opt("ExecCondition", "ExecCondition", section="Service", option_type=STRING, multiline=True),
        _opt("ExecReload", "ExecReload", section="Service", option_type=STRING, multiline=True),
        _opt("ExecStop", "ExecStop", section="Service", option_type=STRING, multiline=True),
        _opt("ExecStopPost", "ExecStopPost", section="Service", option_type=STRING, multiline=True),
        _opt("Restart", "Restart policy", section="Service", option_type=STRING,
             enum=["no", "on-success", "on-failure", "on-abnormal", "on-watchdog", "on-abort", "always"],
             default="no"),
        _opt("RestartSec", "Restart delay", section="Service", option_type=DURATION, default=100),
        _opt("RestartSteps", "Restart steps", section="Service", option_type=UNSIGNED),
        _opt("RestartMaxDelaySec", "Max restart delay", section="Service", option_type=DURATION),
        _opt("TimeoutStartSec", "Start timeout", section="Service", option_type=DURATION, default=90),
        _opt("TimeoutStopSec", "Stop timeout", section="Service", option_type=DURATION, default=90),
        _opt("TimeoutSec", "Timeout (start+stop)", section="Service", option_type=DURATION),
        _opt("TimeoutAbortSec", "Abort timeout", section="Service", option_type=DURATION),
        _opt("RuntimeMaxSec", "Max runtime", section="Service", option_type=DURATION),
        _opt("WatchdogSec", "Watchdog interval", section="Service", option_type=DURATION),
        _opt("WatchdogSignal", "Watchdog signal", section="Service", option_type=STRING,
             enum=["SIGABRT", "SIGALRM", "SIGBUS", "SIGFPE", "SIGHUP", "SIGILL", "SIGINT", "SIGPIPE",
                   "SIGQUIT", "SIGSEGV", "SIGSTKFLT", "SIGSYS", "SIGTERM", "SIGTRAP", "SIGUSR1", "SIGUSR2"]),
        _opt("SuccessExitStatus", "Success exit codes", section="Service", option_type=STRING),
        _opt("RestartPreventExitStatus", "Restart prevent exit", section="Service", option_type=STRING),
        _opt("RestartForceExitStatus", "Restart force exit", section="Service", option_type=STRING),
        _opt("PermissionsStartOnly", "Perms start only", section="Service", option_type=BOOLEAN),
        _opt("RootDirectoryStartOnly", "Root dir start only", section="Service", option_type=BOOLEAN),
        _opt("NonBlocking", "Non-blocking", section="Service", option_type=BOOLEAN),
        _opt("NotifyAccess", "Notify access", section="Service", option_type=STRING,
             enum=["none", "main", "exec", "all"], default="main"),
        _opt("Sockets", "Socket units", section="Service", option_type=STRING, multiline=True),
        _opt("FileDescriptorStoreMax", "FD store max", section="Service", option_type=UNSIGNED, default=0),
        _opt("USBFunctionDescriptors", "USB descriptors", section="Service", option_type=STRING, multiline=True),
        _opt("USBFunctionStrings", "USB strings", section="Service", option_type=STRING, multiline=True),
        _opt("OOMScoreAdjust", "OOM score adjust", section="Service", option_type=INTEGER),
        _opt("OOMPolicy", "OOM policy", section="Service", option_type=STRING,
             enum=["continue", "stop", "kill"], default="stop"),
        _opt("StandardInput", "Standard input", section="Service", option_type=STRING,
             enum=["null", "tty", "tty-force", "tty-fail", "data", "file", "socket", "fd", "journal", "journal+console", "pipe", "inherit"],
             default="null"),
        _opt("StandardOutput", "Standard output", section="Service", option_type=STRING,
             enum=["inherit", "null", "tty", "journal", "journal+console", "kmsg", "kmsg+console", "file", "append", "socket", "fd"],
             default="journal"),
        _opt("StandardError", "Standard error", section="Service", option_type=STRING,
             enum=["inherit", "null", "tty", "journal", "journal+console", "kmsg", "kmsg+console", "file", "append", "socket", "fd"],
             default="journal"),
        _opt("StandardInputData", "Stdin data", section="Service", option_type=STRING, multiline=True),
        _opt("StandardInputText", "Stdin text", section="Service", option_type=STRING, multiline=True),
        _opt("TTYPath", "TTY path", section="Service", option_type=STRING, default="/dev/console"),
        _opt("TTYReset", "TTY reset", section="Service", option_type=BOOLEAN),
        _opt("TTYVHangup", "TTY hangup", section="Service", option_type=BOOLEAN),
        _opt("TTYVTDisallocate", "TTY VT dealloc", section="Service", option_type=BOOLEAN),
        _opt("TTYRows", "TTY rows", section="Service", option_type=UNSIGNED),
        _opt("TTYColumns", "TTY columns", section="Service", option_type=UNSIGNED),
    ]:
        service.add(o)

    exec_common = [
        _opt("WorkingDirectory", "Working directory", section="Service", option_type=STRING),
        _opt("RootDirectory", "Root directory", section="Service", option_type=STRING),
        _opt("RootImage", "Root image", section="Service", option_type=STRING),
        _opt("RootHash", "Root hash", section="Service", option_type=STRING),
        _opt("RootVerity", "Root verity", section="Service", option_type=STRING),
        _opt("MountAPIVFS", "Mount API VFS", section="Service", option_type=BOOLEAN),
        _opt("BindPaths", "Bind mount paths", section="Service", option_type=STRING, multiline=True),
        _opt("BindReadOnlyPaths", "Bind mount r/o", section="Service", option_type=STRING, multiline=True),
        _opt("TemporaryFileSystem", "Temp filesystem", section="Service", option_type=STRING, multiline=True),
        _opt("MountImages", "Mount images", section="Service", option_type=STRING, multiline=True),
        _opt("User", "User", section="Service", option_type=STRING),
        _opt("Group", "Group", section="Service", option_type=STRING),
        _opt("DynamicUser", "Dynamic user", section="Service", option_type=BOOLEAN),
        _opt("SupplementaryGroups", "Extra groups", section="Service", option_type=STRING),
        _opt("Nice", "Nice level", section="Service", option_type=INTEGER),
        _opt("IOSchedulingClass", "I/O class", section="Service", option_type=UNSIGNED),
        _opt("IOSchedulingPriority", "I/O priority", section="Service", option_type=UNSIGNED),
        _opt("CPUSchedulingPolicy", "CPU policy", section="Service", option_type=STRING,
             enum=["other", "batch", "idle", "fifo", "rr"]),
        _opt("CPUSchedulingPriority", "CPU priority", section="Service", option_type=UNSIGNED),
        _opt("CPUSchedulingResetOnFork", "CPU sched reset", section="Service", option_type=BOOLEAN),
        _opt("CPUAffinity", "CPU affinity", section="Service", option_type=STRING),
        _opt("NUMAPolicy", "NUMA policy", section="Service", option_type=STRING,
             enum=["default", "preferred", "bind", "interleave", "local"]),
        _opt("NUMAMask", "NUMA mask", section="Service", option_type=STRING),
        _opt("CapabilityBoundingSet", "Capability set", section="Service", option_type=STRING),
        _opt("AmbientCapabilities", "Ambient caps", section="Service", option_type=STRING),
        _opt("SecureBits", "Secure bits", section="Service", option_type=INTEGER),
        _opt("Capabilities", "Capabilities (legacy)", section="Service", option_type=STRING),
        _opt("LimitCPU", "Limit CPU", section="Service", option_type=STRING),
        _opt("LimitFSIZE", "Limit file size", section="Service", option_type=SIZE),
        _opt("LimitDATA", "Limit data", section="Service", option_type=SIZE),
        _opt("LimitSTACK", "Limit stack", section="Service", option_type=SIZE),
        _opt("LimitCORE", "Limit core", section="Service", option_type=SIZE),
        _opt("LimitRSS", "Limit RSS", section="Service", option_type=SIZE),
        _opt("LimitNOFILE", "Limit files", section="Service", option_type=UNSIGNED),
        _opt("LimitAS", "Limit address space", section="Service", option_type=SIZE),
        _opt("LimitNPROC", "Limit processes", section="Service", option_type=UNSIGNED),
        _opt("LimitMEMLOCK", "Limit memlock", section="Service", option_type=SIZE),
        _opt("LimitLOCKS", "Limit locks", section="Service", option_type=UNSIGNED),
        _opt("LimitSIGPENDING", "Limit sigpending", section="Service", option_type=UNSIGNED),
        _opt("LimitMSGQUEUE", "Limit msgqueue", section="Service", option_type=SIZE),
        _opt("LimitNICE", "Limit nice", section="Service", option_type=INTEGER),
        _opt("LimitRTPRIO", "Limit rtprio", section="Service", option_type=UNSIGNED),
        _opt("LimitRTTIME", "Limit rttime", section="Service", option_type=DURATION),
        _opt("PAMName", "PAM name", section="Service", option_type=STRING),
        _opt("EnvironmentFile", "Env files", section="Service", option_type=STRING, multiline=True),
        _opt("Environment", "Environment", section="Service", option_type=STRING, multiline=True),
        _opt("PassEnvironment", "Pass env", section="Service", option_type=STRING, multiline=True),
        _opt("UnsetEnvironment", "Unset env", section="Service", option_type=STRING, multiline=True),
        _opt("KeyringMode", "Keyring mode", section="Service", option_type=STRING,
             enum=["inherit", "private", "shared"]),
        _opt("SyslogIdentifier", "Syslog ID", section="Service", option_type=STRING),
        _opt("SyslogFacility", "Syslog facility", section="Service", option_type=STRING,
             enum=["kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "clock",
                   "authpriv", "ftp", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7"]),
        _opt("SyslogLevel", "Syslog level", section="Service", option_type=STRING,
             enum=["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]),
        _opt("SyslogLevelPrefix", "Syslog prefix", section="Service", option_type=BOOLEAN, default=True),
        _opt("LogNamespace", "Log namespace", section="Service", option_type=STRING),
        _opt("LogRateLimitIntervalSec", "Log rate interval", section="Service", option_type=DURATION),
        _opt("LogRateLimitBurst", "Log rate burst", section="Service", option_type=UNSIGNED),
        _opt("LogExtraFields", "Extra log fields", section="Service", option_type=STRING, multiline=True),
        _opt("UtmpIdentifier", "Utmp ID", section="Service", option_type=STRING),
        _opt("UtmpMode", "Utmp mode", section="Service", option_type=STRING, enum=["init", "login", "user"]),
        _opt("SELinuxContext", "SELinux context", section="Service", option_type=STRING),
        _opt("AppArmorProfile", "AppArmor profile", section="Service", option_type=STRING),
        _opt("SmackProcessLabel", "Smack label", section="Service", option_type=STRING),
        _opt("IgnoreSIGPIPE", "Ignore SIGPIPE", section="Service", option_type=BOOLEAN, default=True),
        _opt("NoNewPrivileges", "No new privs", section="Service", option_type=BOOLEAN),
        _opt("SystemCallFilter", "Syscall filter", section="Service", option_type=STRING, multiline=True),
        _opt("SystemCallArchitectures", "Syscall archs", section="Service", option_type=STRING),
        _opt("SystemCallErrorNumber", "Syscall errno", section="Service", option_type=INTEGER, default=0),
        _opt("SystemCallLog", "Syscall log", section="Service", option_type=BOOLEAN),
        _opt("MemoryDenyWriteExecute", "Deny W^X", section="Service", option_type=BOOLEAN),
        _opt("RestrictNamespaces", "Restrict ns", section="Service", option_type=BOOLEAN),
        _opt("RestrictRealtime", "Restrict RT", section="Service", option_type=BOOLEAN),
        _opt("RestrictSUIDSGID", "Restrict SUID/SGID", section="Service", option_type=BOOLEAN),
        _opt("RestrictAddressFamilies", "Restrict AF", section="Service", option_type=STRING, multiline=True),
        _opt("LockPersonality", "Lock personality", section="Service", option_type=BOOLEAN),
        _opt("ProtectSystem", "Protect system", section="Service", option_type=STRING,
             enum=["no", "yes", "full", "strict"], default="no"),
        _opt("ProtectHome", "Protect home", section="Service", option_type=STRING,
             enum=["no", "yes", "read-only", "tmpfs"], default="no"),
        _opt("ProtectKernelTunables", "Protect kernel tunables", section="Service", option_type=BOOLEAN),
        _opt("ProtectKernelModules", "Protect modules", section="Service", option_type=BOOLEAN),
        _opt("ProtectKernelLogs", "Protect kernel logs", section="Service", option_type=BOOLEAN),
        _opt("ProtectClock", "Protect clock", section="Service", option_type=BOOLEAN),
        _opt("ProtectControlGroups", "Protect cgroups", section="Service", option_type=BOOLEAN),
        _opt("ProtectProc", "Protect /proc", section="Service", option_type=STRING,
             enum=["no", "yes", "invisible", "ptraceable"]),
        _opt("ProcSubset", "Proc subset", section="Service", option_type=STRING, enum=["all", "pid"]),
        _opt("PrivateTmp", "Private /tmp", section="Service", option_type=BOOLEAN),
        _opt("PrivateDevices", "Private /dev", section="Service", option_type=BOOLEAN),
        _opt("PrivateMounts", "Private mounts", section="Service", option_type=BOOLEAN),
        _opt("PrivateIPC", "Private IPC", section="Service", option_type=BOOLEAN),
        _opt("PrivateUsers", "Private user ns", section="Service", option_type=BOOLEAN),
        _opt("PrivateNetwork", "Private network", section="Service", option_type=BOOLEAN),
        _opt("NetworkNamespacePath", "Net ns path", section="Service", option_type=STRING),
        _opt("IPAddressDeny", "IP deny", section="Service", option_type=STRING, multiline=True),
        _opt("IPAddressAllow", "IP allow", section="Service", option_type=STRING, multiline=True),
        _opt("DevicePolicy", "Device policy", section="Service", option_type=STRING,
             enum=["auto", "closed", "strict"]),
        _opt("DeviceAllow", "Device allow", section="Service", option_type=STRING, multiline=True),
        _opt("SocketBindDeny", "Socket bind deny", section="Service", option_type=STRING, multiline=True),
        _opt("SocketBindAllow", "Socket bind allow", section="Service", option_type=STRING, multiline=True),
        _opt("Service", "Service (dummy)", section="Service", option_type=STRING),
        _opt("ReloadSignal", "Reload signal", section="Service", option_type=STRING,
             enum=["SIGHUP", "SIGUSR1", "SIGUSR2", "SIGINT", "SIGTERM", "SIGQUIT",
                   "SIGABRT", "SIGALRM", "SIGPIPE", "SIGCHLD", "SIGCONT", "SIGSTOP",
                   "SIGTSTP", "SIGTTIN", "SIGTTOU"]),
        _opt("AssertResult", "Assert result", section="Service", option_type=BOOLEAN),
    ]

    resource_opts = [
        _opt("CPUAccounting", "CPU accounting", section="Service", option_type=BOOLEAN),
        _opt("CPUWeight", "CPU weight", section="Service", option_type=UNSIGNED),
        _opt("CPUQuotaPerSecSec", "CPU quota", section="Service", option_type=STRING),
        _opt("CPUQuotaPeriodSec", "CPU quota period", section="Service", option_type=DURATION),
        _opt("CPUShares", "CPU shares", section="Service", option_type=UNSIGNED),
        _opt("StartupCPUWeight", "Startup CPU weight", section="Service", option_type=UNSIGNED),
        _opt("StartupCPUShares", "Startup CPU shares", section="Service", option_type=UNSIGNED),
        _opt("IOAccounting", "I/O accounting", section="Service", option_type=BOOLEAN),
        _opt("IOWeight", "I/O weight", section="Service", option_type=UNSIGNED),
        _opt("StartupIOWeight", "Startup I/O weight", section="Service", option_type=UNSIGNED),
        _opt("IODeviceWeight", "I/O device weight", section="Service", option_type=STRING, multiline=True),
        _opt("IOReadBandwidthMax", "I/O read max", section="Service", option_type=STRING, multiline=True),
        _opt("IOWriteBandwidthMax", "I/O write max", section="Service", option_type=STRING, multiline=True),
        _opt("IOReadIOPSMax", "I/O read IOPS", section="Service", option_type=STRING, multiline=True),
        _opt("IOWriteIOPSMax", "I/O write IOPS", section="Service", option_type=STRING, multiline=True),
        _opt("IODeviceLatencyTargetSec", "I/O latency target", section="Service", option_type=STRING, multiline=True),
        _opt("MemoryAccounting", "Memory accounting", section="Service", option_type=BOOLEAN),
        _opt("MemoryHigh", "Memory high", section="Service", option_type=SIZE),
        _opt("MemoryMax", "Memory max", section="Service", option_type=SIZE),
        _opt("MemorySwapMax", "Swap max", section="Service", option_type=SIZE),
        _opt("MemoryZSwapMax", "Zswap max", section="Service", option_type=SIZE),
        _opt("MemoryLow", "Memory low", section="Service", option_type=SIZE),
        _opt("MemoryMin", "Memory min", section="Service", option_type=SIZE),
        _opt("MemoryLimit", "Memory limit", section="Service", option_type=SIZE),
        _opt("TasksAccounting", "Tasks accounting", section="Service", option_type=BOOLEAN),
        _opt("TasksMax", "Tasks max", section="Service", option_type=UNSIGNED),
        _opt("IPAccounting", "IP accounting", section="Service", option_type=BOOLEAN),
        _opt("ManagedOOMSwap", "OOM swap", section="Service", option_type=STRING, enum=["auto", "kill", "none"]),
        _opt("ManagedOOMMemoryPressure", "OOM mem pressure", section="Service", option_type=STRING, enum=["auto", "kill", "none"]),
        _opt("ManagedOOMMemoryPressureLimitPercent", "OOM pressure %", section="Service", option_type=PERCENT),
        _opt("CGroupMask", "CGroup mask", section="Service", option_type=STRING),
    ]

    for o in exec_common + resource_opts:
        service.add(o)
    sections["Service"] = service

    install = SectionDef("Install", SECTION_DESCRIPTIONS["Install"])
    for o in [
        _opt("WantedBy", "Wanted by", section="Install", option_type=STRING, multiline=True),
        _opt("RequiredBy", "Required by", section="Install", option_type=STRING, multiline=True),
        _opt("Also", "Also installs", section="Install", option_type=STRING, multiline=True),
        _opt("DefaultInstance", "Default instance", section="Install", option_type=STRING),
        _opt("Alias", "Alias names", section="Install", option_type=STRING, multiline=True),
    ]:
        install.add(o)
    sections["Install"] = install

    socket = SectionDef("Socket", SECTION_DESCRIPTIONS["Socket"])
    for o in [
        _opt("ListenStream", "Listen (stream)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenDatagram", "Listen (datagram)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenSequentialPacket", "Listen (seq)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenFIFO", "Listen (FIFO)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenSpecial", "Listen (special)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenNetlink", "Listen (netlink)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenMessageQueue", "Listen (msgqueue)", section="Socket", option_type=STRING, multiline=True),
        _opt("ListenUSBFunction", "Listen (USB)", section="Socket", option_type=STRING, multiline=True),
        _opt("SocketProtocol", "Socket protocol", section="Socket", option_type=STRING),
        _opt("BindIPv6Only", "Bind IPv6 only", section="Socket", option_type=STRING, enum=["default", "both", "ipv6-only"]),
        _opt("BindToDevice", "Bind to device", section="Socket", option_type=STRING),
        _opt("Backlog", "Backlog", section="Socket", option_type=UNSIGNED),
        _opt("SocketUser", "Socket user", section="Socket", option_type=STRING),
        _opt("SocketGroup", "Socket group", section="Socket", option_type=STRING),
        _opt("SocketMode", "Socket mode", section="Socket", option_type=STRING, default="0666"),
        _opt("DirectoryMode", "Directory mode", section="Socket", option_type=STRING, default="0755"),
        _opt("Accept", "Accept", section="Socket", option_type=BOOLEAN),
        _opt("MaxConnections", "Max connections", section="Socket", option_type=UNSIGNED, default=64),
        _opt("MaxConnectionsPerSource", "Max conns/source", section="Socket", option_type=UNSIGNED),
        _opt("KeepAlive", "Keep alive", section="Socket", option_type=BOOLEAN),
        _opt("KeepAliveTimeSec", "KA time", section="Socket", option_type=DURATION),
        _opt("KeepAliveIntervalSec", "KA interval", section="Socket", option_type=DURATION),
        _opt("KeepAliveProbes", "KA probes", section="Socket", option_type=UNSIGNED),
        _opt("Service", "Activated service", section="Socket", option_type=STRING),
        _opt("RemoveOnStop", "Remove on stop", section="Socket", option_type=BOOLEAN, default=True),
        _opt("NoDelay", "No delay", section="Socket", option_type=BOOLEAN),
        _opt("Priority", "Priority", section="Socket", option_type=UNSIGNED),
        _opt("DeferAcceptSec", "Defer accept", section="Socket", option_type=DURATION),
        _opt("Writable", "Writable", section="Socket", option_type=BOOLEAN),
        _opt("FlushPending", "Flush pending", section="Socket", option_type=BOOLEAN),
        _opt("ReceiveBuffer", "Receive buffer", section="Socket", option_type=SIZE),
        _opt("SendBuffer", "Send buffer", section="Socket", option_type=SIZE),
        _opt("IPTOS", "IP TOS", section="Socket", option_type=UNSIGNED),
        _opt("IPTTL", "IP TTL", section="Socket", option_type=UNSIGNED),
        _opt("Mark", "Mark", section="Socket", option_type=STRING),
        _opt("PipeSize", "Pipe size", section="Socket", option_type=SIZE),
        _opt("FreeBind", "Free bind", section="Socket", option_type=BOOLEAN),
        _opt("Transparent", "Transparent proxy", section="Socket", option_type=BOOLEAN),
        _opt("Broadcast", "Broadcast", section="Socket", option_type=BOOLEAN),
        _opt("PassCredentials", "Pass credentials", section="Socket", option_type=BOOLEAN),
        _opt("PassSecurity", "Pass security", section="Socket", option_type=BOOLEAN),
        _opt("PassPacketInfo", "Pass packet info", section="Socket", option_type=BOOLEAN),
        _opt("Timestamping", "Timestamping", section="Socket", option_type=STRING),
        _opt("SELinuxContextFromNet", "SELinux from net", section="Socket", option_type=BOOLEAN),
        _opt("Symlinks", "Symlinks", section="Socket", option_type=STRING, multiline=True),
        _opt("MessageQueueMaxMessages", "MQ max msgs", section="Socket", option_type=UNSIGNED),
        _opt("MessageQueueMessageSize", "MQ msg size", section="Socket", option_type=SIZE),
        _opt("TCPCongestion", "TCP congestion", section="Socket", option_type=STRING),
    ]:
        socket.add(o)
    sections["Socket"] = socket

    timer = SectionDef("Timer", SECTION_DESCRIPTIONS["Timer"])
    for o in [
        _opt("OnActiveSec", "On active", section="Timer", option_type=DURATION),
        _opt("OnBootSec", "On boot", section="Timer", option_type=DURATION),
        _opt("OnStartupSec", "On startup", section="Timer", option_type=DURATION),
        _opt("OnUnitActiveSec", "On unit active", section="Timer", option_type=DURATION),
        _opt("OnUnitInactiveSec", "On unit inactive", section="Timer", option_type=DURATION),
        _opt("OnCalendar", "Calendar", section="Timer", option_type=STRING, multiline=True),
        _opt("AccuracySec", "Accuracy", section="Timer", option_type=DURATION, default=60),
        _opt("RandomizedDelaySec", "Random delay", section="Timer", option_type=DURATION),
        _opt("FixedRandomDelay", "Fixed random", section="Timer", option_type=BOOLEAN),
        _opt("Unit", "Unit to activate", section="Timer", option_type=STRING),
        _opt("Persistent", "Persistent", section="Timer", option_type=BOOLEAN),
        _opt("WakeSystem", "Wake system", section="Timer", option_type=BOOLEAN),
        _opt("RemainAfterElapse", "Remain after", section="Timer", option_type=BOOLEAN),
    ]:
        timer.add(o)
    sections["Timer"] = timer

    path = SectionDef("Path", SECTION_DESCRIPTIONS["Path"])
    for o in [
        _opt("PathExists", "Path exists", section="Path", option_type=STRING, multiline=True),
        _opt("PathExistsGlob", "Path exists (glob)", section="Path", option_type=STRING, multiline=True),
        _opt("PathChanged", "Path changed", section="Path", option_type=STRING, multiline=True),
        _opt("PathModified", "Path modified", section="Path", option_type=STRING, multiline=True),
        _opt("DirectoryNotEmpty", "Dir not empty", section="Path", option_type=STRING, multiline=True),
        _opt("Unit", "Unit to activate", section="Path", option_type=STRING),
        _opt("MakeDirectory", "Make dir", section="Path", option_type=BOOLEAN),
        _opt("DirectoryMode", "Dir mode", section="Path", option_type=STRING, default="0755"),
    ]:
        path.add(o)
    sections["Path"] = path

    mount = SectionDef("Mount", SECTION_DESCRIPTIONS["Mount"])
    for o in [
        _opt("What", "What to mount", section="Mount", option_type=STRING),
        _opt("Where", "Mount point", section="Mount", option_type=STRING),
        _opt("Type", "Filesystem type", section="Mount", option_type=STRING),
        _opt("Options", "Options", section="Mount", option_type=STRING),
        _opt("SloppyOptions", "Sloppy options", section="Mount", option_type=BOOLEAN),
        _opt("LazyUnmount", "Lazy unmount", section="Mount", option_type=BOOLEAN),
        _opt("ForceUnmount", "Force unmount", section="Mount", option_type=BOOLEAN),
        _opt("ReadWriteOnly", "Read-write only", section="Mount", option_type=BOOLEAN),
        _opt("DefaultDependencies", "Default deps", section="Mount", option_type=BOOLEAN, default=True),
        _opt("TimeoutSec", "Timeout", section="Mount", option_type=DURATION),
        _opt("DirectoryMode", "Dir mode", section="Mount", option_type=STRING, default="0755"),
        _opt("UtmpMode", "Utmp mode", section="Mount", option_type=STRING),
    ]:
        mount.add(o)
    sections["Mount"] = mount

    automount = SectionDef("Automount", SECTION_DESCRIPTIONS["Automount"])
    for o in [
        _opt("Where", "Mount point", section="Automount", option_type=STRING),
        _opt("DirectoryMode", "Dir mode", section="Automount", option_type=STRING, default="0755"),
        _opt("TimeoutIdleSec", "Idle timeout", section="Automount", option_type=DURATION),
        _opt("ExtraBindMounts", "Extra bind mounts", section="Automount", option_type=STRING, multiline=True),
    ]:
        automount.add(o)
    sections["Automount"] = automount

    swap = SectionDef("Swap", SECTION_DESCRIPTIONS["Swap"])
    for o in [
        _opt("What", "Swap device", section="Swap", option_type=STRING),
        _opt("Priority", "Priority", section="Swap", option_type=INTEGER),
        _opt("Options", "Options", section="Swap", option_type=STRING),
        _opt("TimeoutSec", "Timeout", section="Swap", option_type=STRING),
    ]:
        swap.add(o)
    sections["Swap"] = swap

    device = SectionDef("Device", SECTION_DESCRIPTIONS["Device"])
    for o in [
        _opt("Description", "Description", section="Device", option_type=STRING),
        _opt("Sysfs", "Sysfs path", section="Device", option_type=STRING),
    ]:
        device.add(o)
    sections["Device"] = device

    sections["Target"] = SectionDef("Target", SECTION_DESCRIPTIONS["Target"])

    scope = SectionDef("Scope", SECTION_DESCRIPTIONS["Scope"])
    for o in resource_opts + [
        _opt("Controller", "Controller", section="Scope", option_type=STRING),
        _opt("TimeoutStopSec", "Stop timeout", section="Scope", option_type=DURATION),
    ]:
        scope.add(o)
    sections["Scope"] = scope

    slice = SectionDef("Slice", SECTION_DESCRIPTIONS["Slice"])
    for o in resource_opts:
        slice.add(o)
    sections["Slice"] = slice

    return sections


ALL_SECTIONS = _build_sections()
SECTION_NAMES = list(ALL_SECTIONS.keys())

DEFAULT_SECTIONS_FOR_TYPE = {
    "service": ["Unit", "Service", "Install"],
    "socket": ["Unit", "Socket", "Install"],
    "timer": ["Unit", "Timer", "Install"],
    "path": ["Unit", "Path", "Install"],
    "mount": ["Unit", "Mount", "Install"],
    "automount": ["Unit", "Automount", "Install"],
    "swap": ["Unit", "Swap", "Install"],
    "device": ["Unit", "Device", "Install"],
    "target": ["Unit", "Install"],
    "scope": ["Unit", "Scope", "Install"],
    "slice": ["Unit", "Slice", "Install"],
}


@dataclass
class UnitFile:
    filename: str = ""
    filepath: str = ""
    unit_type: str = "service"
    values: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.values:
            self.values = {}
            for sec_name in self.default_sections:
                self.values[sec_name] = {}

    @property
    def default_sections(self):
        return DEFAULT_SECTIONS_FOR_TYPE.get(self.unit_type, ["Unit", "Install"])

    def set_value(self, section, key, value):
        if section not in self.values:
            self.values[section] = {}
        self.values[section][key] = value

    def get_value(self, section, key):
        return self.values.get(section, {}).get(key)

    def get_option(self, section, key):
        sec = ALL_SECTIONS.get(section)
        if sec:
            return sec.options.get(key)
        return None

    def to_unit_file_content(self):
        lines = []
        for sec_name in self.values:
            sec = ALL_SECTIONS.get(sec_name)
            if sec:
                content = sec.to_ini(self.values[sec_name])
                if content.strip():
                    lines.append(content)
        return "\n".join(lines)

    @classmethod
    def from_unit_file_content(cls, content, filename=""):
        unit = cls(filename=filename)
        current_section = None
        current_key = None
        current_lines = []

        for line in content.split("\n"):
            line_s = line.strip()
            sec_match = re.match(r"^\[(.+)\]$", line_s)
            if sec_match:
                if current_section and current_key and current_lines:
                    opt = unit.get_option(current_section, current_key)
                    if opt:
                        val = opt.from_ini("\n".join(current_lines))
                        unit.set_value(current_section, current_key, val)
                    current_lines = []
                current_section = sec_match.group(1)
                if current_section not in unit.values:
                    unit.values[current_section] = {}
                current_key = None
                continue

            if line_s == "" or line_s.startswith("#") or line_s.startswith(";"):
                continue

            if line.startswith(" ") or line.startswith("\t"):
                if current_key is not None:
                    current_lines.append(line_s)
                continue

            if current_section and current_key:
                opt = unit.get_option(current_section, current_key)
                if opt:
                    val = opt.from_ini("\n".join(current_lines))
                    unit.set_value(current_section, current_key, val)
                current_lines = []

            if "=" in line_s:
                current_key, _, raw_val = line_s.partition("=")
                current_key = current_key.strip()
                raw_val = raw_val.strip()
                current_lines = [raw_val] if raw_val else []

        if current_section and current_key and current_lines:
            opt = unit.get_option(current_section, current_key)
            if opt:
                val = opt.from_ini("\n".join(current_lines))
                unit.set_value(current_section, current_key, val)

        return unit


def get_unit_type_enum():
    return ["service", "socket", "timer", "path", "mount", "automount", "swap", "device", "target", "scope", "slice"]