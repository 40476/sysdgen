import subprocess
import os
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from .models import UnitFile


SYSTEMD_PATHS = {
    "system": Path("/etc/systemd/system"),
    "user": Path(os.path.expanduser("~/.config/systemd/user")),
}


class SystemdManager:
    """Manages interaction with systemd units on the system."""

    def __init__(self, scope: str = "system"):
        self.scope = scope
        self.base_path = SYSTEMD_PATHS[scope]
        self._systemctl_base = ["systemctl"] if scope == "system" else ["systemctl", "--user"]

    # ─── Unit file loading ───────────────────────────────────────

    def list_unit_files(self) -> List[Dict[str, str]]:
        """List all available unit files on the system."""
        try:
            result = subprocess.run(
                self._systemctl_base + ["list-unit-files", "--no-legend"],
                capture_output=True, text=True, timeout=10
            )
            units = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.strip().split()
                if len(parts) >= 2:
                    units.append({
                        "name": parts[0],
                        "state": parts[1],
                        "path": self._find_unit_path(parts[0]),
                    })
            return units
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def list_units(self) -> List[Dict[str, str]]:
        """List active/inactive units with status."""
        try:
            result = subprocess.run(
                self._systemctl_base + ["list-units", "--all", "--no-legend"],
                capture_output=True, text=True, timeout=10
            )
            units = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.strip().split(maxsplit=4)
                if len(parts) >= 4:
                    units.append({
                        "name": parts[0],
                        "load": parts[1],
                        "active": parts[2],
                        "sub": parts[3],
                        "description": parts[4] if len(parts) > 4 else "",
                    })
            return units
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def _find_unit_path(self, unit_name: str) -> str:
        """Find the full path of a unit file."""
        try:
            result = subprocess.run(
                self._systemctl_base + ["show", "--property=FragmentPath", unit_name],
                capture_output=True, text=True, timeout=5
            )
            if "=" in result.stdout.strip():
                return result.stdout.strip().split("=", 1)[1]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""

    def load_unit_file(self, unit_name: str) -> Optional[UnitFile]:
        """Load a unit file from the system."""
        path = self._find_unit_path(unit_name)
        if not path:
            return None
        try:
            with open(path, "r") as f:
                content = f.read()
            unit = UnitFile.from_unit_file_content(content, filename=unit_name)
            unit.filepath = path
            # Determine unit type
            ext = unit_name.rsplit(".", 1)[-1] if "." in unit_name else "service"
            unit.unit_type = ext
            return unit
        except (IOError, OSError):
            return None

    def load_unit_file_from_path(self, path: str) -> Optional[UnitFile]:
        """Load a unit file from an arbitrary path."""
        try:
            with open(path, "r") as f:
                content = f.read()
            filename = os.path.basename(path)
            unit = UnitFile.from_unit_file_content(content, filename=filename)
            unit.filepath = path
            ext = filename.rsplit(".", 1)[-1] if "." in filename else "service"
            unit.unit_type = ext
            return unit
        except (IOError, OSError):
            return None

    # ─── Save / write ────────────────────────────────────────────

    def save_unit_file(self, unit: UnitFile, path: Optional[str] = None) -> bool:
        """Save a unit file to disk."""
        save_path = path or unit.filepath or str(self.base_path / unit.filename)
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w") as f:
                f.write(unit.to_unit_file_content())
            unit.filepath = save_path
            return True
        except (IOError, OSError):
            return False

    # ─── systemctl operations ────────────────────────────────────

    def _run_systemctl(self, args: List[str]) -> Tuple[bool, str]:
        try:
            result = subprocess.run(
                self._systemctl_base + args,
                capture_output=True, text=True, timeout=30
            )
            success = result.returncode == 0
            output = result.stdout.strip() or result.stderr.strip()
            return success, output
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except FileNotFoundError:
            return False, "systemctl not found"

    def start_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["start", unit_name])

    def stop_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["stop", unit_name])

    def restart_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["restart", unit_name])

    def reload_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["reload", unit_name])

    def enable_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["enable", unit_name])

    def disable_unit(self, unit_name: str) -> Tuple[bool, str]:
        return self._run_systemctl(["disable", unit_name])

    def daemon_reload(self) -> Tuple[bool, str]:
        return self._run_systemctl(["daemon-reload"])

    def get_unit_status(self, unit_name: str) -> str:
        """Get detailed status output for a unit."""
        try:
            result = subprocess.run(
                self._systemctl_base + ["status", unit_name],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return "Command timed out"
        except FileNotFoundError:
            return "systemctl not found"

    def is_active(self, unit_name: str) -> bool:
        try:
            result = subprocess.run(
                self._systemctl_base + ["is-active", unit_name],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() == "active"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def is_enabled(self, unit_name: str) -> bool:
        try:
            result = subprocess.run(
                self._systemctl_base + ["is-enabled", unit_name],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() == "enabled"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_property(self, unit_name: str, prop: str) -> str:
        try:
            result = subprocess.run(
                self._systemctl_base + ["show", "--property=" + prop, unit_name],
                capture_output=True, text=True, timeout=5
            )
            if "=" in result.stdout.strip():
                return result.stdout.strip().split("=", 1)[1]
            return ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    # ─── Journalctl ──────────────────────────────────────────────

    def get_journal_log(
        self, unit_name: str, lines: int = 100,
        follow: bool = False, priority: Optional[str] = None
    ) -> List[str]:
        """Get journal log entries for a unit."""
        try:
            cmd = ["journalctl", "-u", unit_name, "-n", str(lines),
                   "--no-pager", "-o", "short-iso"]
            if self.scope == "user":
                cmd.append("--user-unit")
                cmd.append(unit_name)
            if priority:
                cmd.extend(["-p", priority])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return result.stdout.strip().split("\n") if result.stdout.strip() else []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ["Error reading journal"]

    # ─── Dependency graph ────────────────────────────────────────

    def get_dependencies(
        self, unit_name: str, reverse: bool = False
    ) -> List[Dict[str, str]]:
        """Get dependencies of a unit."""
        try:
            prop = "ConsistsOf" if reverse else "Dependencies"
            result = subprocess.run(
                self._systemctl_base + ["show", "--property=" + prop, unit_name],
                capture_output=True, text=True, timeout=10
            )
            if "=" not in result.stdout.strip():
                return []
            deps_str = result.stdout.strip().split("=", 1)[1]
            deps = []
            for dep in deps_str.split():
                dep = dep.strip()
                if dep:
                    # Get type by the prefix
                    parts = dep.split(":")
                    if len(parts) > 1:
                        deps.append({"name": parts[1], "type": parts[0]})
                    else:
                        deps.append({"name": dep, "type": "unknown"})
            return deps
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def get_all_dependencies(
        self, unit_name: str, max_depth: int = 3, reverse: bool = False
    ) -> Dict[str, List[Dict]]:
        """Get a full dependency tree."""
        tree = {}
        visited = set()

        def _walk(name: str, depth: int):
            if name in visited or depth > max_depth:
                return
            visited.add(name)
            deps = self.get_dependencies(name, reverse=reverse)
            tree[name] = deps
            for dep in deps:
                _walk(dep["name"], depth + 1)

        _walk(unit_name, 0)
        return tree

    # ─── File system operations ──────────────────────────────────

    def list_directory(self, path: Optional[str] = None) -> List[Dict]:
        """List unit files in a directory."""
        dir_path = path or str(self.base_path)
        try:
            items = []
            for f in sorted(Path(dir_path).iterdir()):
                if f.suffix in (".service", ".socket", ".timer", ".path",
                                ".mount", ".automount", ".swap", ".target",
                                ".device", ".scope", ".slice"):
                    items.append({
                        "name": f.name,
                        "path": str(f),
                        "is_dir": f.is_dir(),
                    })
            return items
        except FileNotFoundError:
            return []

    def edit_unit(self, unit_name: str) -> bool:
        """Open systemctl edit for a unit (returns True if successful)."""
        try:
            subprocess.run(
                self._systemctl_base + ["edit", unit_name],
                timeout=5
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False