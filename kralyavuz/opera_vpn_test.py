import json
import re
import subprocess
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from .platform_paths import is_opera_gx_command


VPN_PROCESS_PATTERN = re.compile(r"vpn|wireguard|openvpn|tunnel", re.IGNORECASE)


def run(command: Sequence[str]) -> Tuple[str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"{type(exc).__name__}: {exc}"
    error = completed.stderr.strip() if completed.returncode else ""
    return completed.stdout.strip(), error


def process_snapshots() -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    output, _ = run(["ps", "-axo", "pid=,comm=,args="])
    opera: List[Dict[str, object]] = []
    vpn_related: List[Dict[str, object]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, command, args = int(parts[0]), parts[1], parts[2]
        is_opera = is_opera_gx_command(args)
        process_type = re.search(r"--type=([^ ]+)", args)
        utility_type = re.search(r"--utility-sub-type=([^ ]+)", args)
        role = "main"
        if utility_type:
            role = utility_type.group(1)
        elif process_type:
            role = process_type.group(1)
        item = {"pid": pid, "command": command, "role": role}
        if is_opera:
            opera.append(item)
        if is_opera and role == "network.mojom.NetworkService":
            vpn_related.append({**item, "reason": "Opera NetworkService"})
        elif not is_opera and VPN_PROCESS_PATTERN.search(command):
            vpn_related.append({**item, "reason": "process name"})
    return opera, vpn_related


def lsof_snapshots(opera_pids: set) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    output, _ = run(["lsof", "-nP", "-iTCP", "-iUDP"])
    listeners: List[Dict[str, object]] = []
    opera_connections: List[Dict[str, object]] = []
    for line in output.splitlines()[1:]:
        parts = line.split(None, 8)
        if len(parts) < 9 or not parts[1].isdigit():
            continue
        pid = int(parts[1])
        name = parts[8]
        item = {
            "process": parts[0],
            "pid": pid,
            "protocol": parts[7],
            "endpoint": name,
        }
        if "(LISTEN)" in name and is_local_listener(name):
            listeners.append(item)
        if pid in opera_pids:
            opera_connections.append(item)
    return unique_items(listeners), unique_items(opera_connections)


def is_local_listener(endpoint: str) -> bool:
    address = endpoint.replace(" (LISTEN)", "")
    return bool(
        re.match(r"^(127(?:\.\d+){3}|localhost):\d+$", address)
        or re.match(r"^\[?::1\]?:\d+$", address)
    )


def unique_items(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    seen = set()
    result = []
    for item in items:
        key = (item["pid"], item["protocol"], item["endpoint"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def route_snapshot() -> Dict[str, object]:
    default_route, default_error = run(["route", "-n", "get", "default"])
    routes, routes_error = run(["netstat", "-rn", "-f", "inet"])
    tunnel_routes = [
        line.strip()
        for line in routes.splitlines()
        if re.search(r"\butun\d*\b|\bppp\d*\b|\btun\d*\b", line)
    ]
    return {
        "default": default_route,
        "tunnel_routes": tunnel_routes,
        "errors": [error for error in (default_error, routes_error) if error],
    }


def scutil_snapshot() -> Dict[str, object]:
    proxy, proxy_error = run(["scutil", "--proxy"])
    network, network_error = run(["scutil", "--nwi"])
    return {
        "proxy": proxy,
        "network": network,
        "errors": [error for error in (proxy_error, network_error) if error],
    }


def proxy_candidates(
    listeners: List[Dict[str, object]],
    opera_pids: set,
    vpn_pids: set,
) -> List[Dict[str, object]]:
    return [
        item
        for item in listeners
        if item["pid"] in opera_pids or item["pid"] in vpn_pids
    ]


def main() -> int:
    opera_processes, vpn_processes = process_snapshots()
    opera_pids = {int(item["pid"]) for item in opera_processes}
    vpn_pids = {int(item["pid"]) for item in vpn_processes}
    listeners, opera_connections = lsof_snapshots(opera_pids)
    routes = route_snapshot()
    network = scutil_snapshot()
    candidates = proxy_candidates(listeners, opera_pids, vpn_pids)

    proxy_text = str(network["proxy"])
    system_proxy_enabled = bool(re.search(r"(?:HTTP|HTTPS|SOCKS)Enable\s*:\s*1", proxy_text))
    system_tunnel_route = bool(routes["tunnel_routes"])
    if candidates:
        scope = "Opera/VPN ilişkili localhost dinleyicisi bulundu; protokol ayrıca doğrulanmalı."
    elif system_proxy_enabled or system_tunnel_route:
        scope = "Sistem proxy veya tünel rotası görülüyor; Opera dışı trafik etkilenebilir."
    else:
        scope = "Yerel proxy ve sistem tünel rotası görülmedi; VPN büyük olasılıkla Opera süreci içinde."

    report = {
        "tested_at": datetime.now().isoformat(),
        "opera_processes": opera_processes,
        "vpn_related_processes": vpn_processes,
        "localhost_listeners": listeners,
        "opera_connections": opera_connections,
        "system_routes": routes,
        "system_network": network,
        "assessment": {
            "proxy_candidates": candidates,
            "system_proxy_enabled": system_proxy_enabled,
            "system_tunnel_route": system_tunnel_route,
            "likely_scope": scope,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
