"""Windows Wi-Fi authentication check; hardware access requires explicit approval.

Import this module from the approved trial. Passwords stay in memory and an
exclusive, short-lived XML file below this worktree's ignored local-data folder.
No password is passed to a command line or included in the returned evidence.
The default module entry point performs offline checks only.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = "http://www.microsoft.com/networking/WLAN/profile/v1"
CONNECTED = 1
DISCONNECTED = 4


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD),
                ("Data4", C.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(str(value).strip("{}")).bytes_le)

    def text(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


class InterfaceInfo(C.Structure):
    _fields_ = [("guid", GUID), ("description", W.WCHAR * 256), ("state", C.c_int)]


class Ssid(C.Structure):
    _fields_ = [("length", W.DWORD), ("data", C.c_ubyte * 32)]


class Association(C.Structure):
    _fields_ = [("ssid", Ssid), ("bss_type", C.c_int), ("bssid", C.c_ubyte * 6),
                ("phy_type", C.c_int), ("phy_index", W.DWORD), ("signal", W.DWORD),
                ("rx_rate", W.DWORD), ("tx_rate", W.DWORD)]


class Security(C.Structure):
    _fields_ = [("enabled", W.BOOL), ("one_x", W.BOOL),
                ("auth", C.c_int), ("cipher", C.c_int)]


class Connection(C.Structure):
    _fields_ = [("state", C.c_int), ("mode", C.c_int), ("profile", W.WCHAR * 256),
                ("association", Association), ("security", Security)]


class ProfileInfo(C.Structure):
    _fields_ = [("name", W.WCHAR * 256), ("flags", W.DWORD)]


class ConnectParams(C.Structure):
    _fields_ = [("mode", C.c_int), ("profile", W.LPCWSTR), ("ssid", C.c_void_p),
                ("bssid_list", C.c_void_p), ("bss_type", C.c_int), ("flags", W.DWORD)]


class Wlan:
    """Small Native WLAN wrapper: status is independent of Windows UI language."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows WLAN API required")
        self.dll = C.WinDLL("wlanapi.dll")
        self.handle = W.HANDLE()
        signatures = {
            "WlanOpenHandle": [W.DWORD, C.c_void_p, C.POINTER(W.DWORD), C.POINTER(W.HANDLE)],
            "WlanCloseHandle": [W.HANDLE, C.c_void_p],
            "WlanEnumInterfaces": [W.HANDLE, C.c_void_p, C.POINTER(C.c_void_p)],
            "WlanGetProfileList": [W.HANDLE, C.POINTER(GUID), C.c_void_p, C.POINTER(C.c_void_p)],
            "WlanQueryInterface": [W.HANDLE, C.POINTER(GUID), C.c_int, C.c_void_p,
                                   C.POINTER(W.DWORD), C.POINTER(C.c_void_p), C.POINTER(C.c_int)],
            "WlanConnect": [W.HANDLE, C.POINTER(GUID), C.POINTER(ConnectParams), C.c_void_p],
            "WlanDisconnect": [W.HANDLE, C.POINTER(GUID), C.c_void_p],
            "WlanDeleteProfile": [W.HANDLE, C.POINTER(GUID), W.LPCWSTR, C.c_void_p],
            "WlanFreeMemory": [C.c_void_p],
        }
        for name, args in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes = args
            function.restype = None if name == "WlanFreeMemory" else W.DWORD
        version = W.DWORD()
        self.check(self.dll.WlanOpenHandle(2, None, C.byref(version), C.byref(self.handle)))

    @staticmethod
    def check(code):
        if code:
            raise RuntimeError("WLAN operation failed, code=" + str(code))

    def close(self):
        if self.handle:
            self.dll.WlanCloseHandle(self.handle, None)
            self.handle = W.HANDLE()

    def interfaces(self):
        pointer = C.c_void_p()
        self.check(self.dll.WlanEnumInterfaces(self.handle, None, C.byref(pointer)))
        try:
            count = C.cast(pointer, C.POINTER(W.DWORD))[0]
            entries = (InterfaceInfo * count).from_address(pointer.value + 8)
            return [{"guid": entry.guid.text(), "alias": self.alias(entry.guid), "description": entry.description,
                     "state": entry.state} for entry in entries]
        finally:
            self.dll.WlanFreeMemory(pointer)

    def alias(self, guid):
        helper = C.WinDLL("iphlpapi.dll")
        helper.ConvertInterfaceGuidToLuid.argtypes = [C.POINTER(GUID), C.POINTER(C.c_ulonglong)]
        helper.ConvertInterfaceGuidToLuid.restype = W.DWORD
        helper.ConvertInterfaceLuidToAlias.argtypes = [C.POINTER(C.c_ulonglong), W.LPWSTR, C.c_size_t]
        helper.ConvertInterfaceLuidToAlias.restype = W.DWORD
        luid, name = C.c_ulonglong(), C.create_unicode_buffer(257)
        self.check(helper.ConvertInterfaceGuidToLuid(C.byref(guid), C.byref(luid)))
        self.check(helper.ConvertInterfaceLuidToAlias(C.byref(luid), name, len(name)))
        return name.value

    def profiles(self, guid):
        pointer = C.c_void_p()
        key = GUID.parse(guid)
        self.check(self.dll.WlanGetProfileList(self.handle, C.byref(key), None, C.byref(pointer)))
        try:
            count = C.cast(pointer, C.POINTER(W.DWORD))[0]
            entries = (ProfileInfo * count).from_address(pointer.value + 8)
            return sorted(entry.name for entry in entries)
        finally:
            self.dll.WlanFreeMemory(pointer)

    def status(self, guid):
        key, size, pointer, opcode_type = GUID.parse(guid), W.DWORD(), C.c_void_p(), C.c_int()
        code = self.dll.WlanQueryInterface(self.handle, C.byref(key), 7, None,
                                           C.byref(size), C.byref(pointer), C.byref(opcode_type))
        if code == 5023:  # ERROR_INVALID_STATE: no active association
            return {"state": DISCONNECTED, "ssid": None, "profile": None, "bssid": None}
        self.check(code)
        try:
            if size.value < C.sizeof(Connection):
                raise RuntimeError("WLAN connection data truncated")
            value = C.cast(pointer, C.POINTER(Connection)).contents
            association = value.association
            if association.ssid.length > 32:
                raise RuntimeError("WLAN SSID length invalid")
            raw_ssid = bytes(association.ssid.data[:association.ssid.length])
            return {"state": value.state, "ssid": raw_ssid.decode("utf-8", "replace"),
                    "ssid_hex": raw_ssid.hex(), "profile": value.profile,
                    "bssid": ":".join(format(n, "02x") for n in association.bssid),
                    "signal": association.signal}
        finally:
            self.dll.WlanFreeMemory(pointer)

    def connect(self, guid, profile):
        key = GUID.parse(guid)
        params = ConnectParams(0, profile, None, None, 1, 0)  # profile / infrastructure
        self.check(self.dll.WlanConnect(self.handle, C.byref(key), C.byref(params), None))

    def disconnect(self, guid):
        key = GUID.parse(guid)
        self.check(self.dll.WlanDisconnect(self.handle, C.byref(key), None))

    def delete_profile(self, guid, name):
        key = GUID.parse(guid)
        self.check(self.dll.WlanDeleteProfile(self.handle, C.byref(key), name, None))


def profile_xml(name: str, ssid: str, password: str) -> bytes:
    encoded = ssid.encode("utf-8")
    if not 1 <= len(encoded) <= 32 or not 8 <= len(password) <= 63:
        raise ValueError("SSID/password length outside WPA2-PSK profile limits")
    if any(ord(char) < 32 for char in name + ssid + password):
        raise ValueError("Control characters are excluded from the temporary profile")
    ET.register_namespace("", NS)
    root = ET.Element("{" + NS + "}WLANProfile")

    def child(parent, tag, text=None):
        element = ET.SubElement(parent, "{" + NS + "}" + tag)
        element.text = text
        return element

    child(root, "name", name)
    config = child(root, "SSIDConfig")
    identifier = child(config, "SSID")
    child(identifier, "hex", encoded.hex().upper())
    child(identifier, "name", ssid)
    child(root, "connectionType", "ESS")
    child(root, "connectionMode", "manual")
    security = child(child(root, "MSM"), "security")
    authentication = child(security, "authEncryption")
    child(authentication, "authentication", "WPA2PSK")
    child(authentication, "encryption", "AES")
    child(authentication, "useOneX", "false")
    key = child(security, "sharedKey")
    child(key, "keyType", "passPhrase")
    child(key, "protected", "false")
    child(key, "keyMaterial", password)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _wait(api, guid, predicate, seconds, stable_seconds=0.0):
    started, stable_since, observations = time.monotonic(), None, []
    while True:
        current = api.status(guid)
        elapsed = time.monotonic() - started
        if not observations or current != observations[-1]["status"]:
            observations.append({"elapsed_s": round(elapsed, 3), "status": current})
        if predicate(current):
            stable_since = stable_since if stable_since is not None else time.monotonic()
            if time.monotonic() - stable_since >= stable_seconds:
                return True, observations
        else:
            stable_since = None
        if elapsed >= seconds:
            return False, observations
        time.sleep(0.5)


def attempt_wifi_connection(*, ssid: str, password: str, profile_path: Path | str,
                            approval: bool, interface_guid: str | None = None,
                            expected_bssid: str | None = None, timeout_s: float = 25.0):
    """Run one isolated association, remove own profile, restore original connection.

    `connected=False` is an unsuccessful attempt, not proof of wrong credentials.
    Use positive-negative-positive on the same device/AP for comparative evidence.
    Approval must come from the parent trial's already validated user approval.
    """
    if approval is not True:
        raise RuntimeError("Explicit approved trial required")
    path = Path(profile_path).resolve()
    if not path.is_relative_to((ROOT / "local-data").resolve()) or path.suffix.lower() != ".xml":
        raise ValueError("Temporary XML must be in this worktree local-data directory")
    if not 5 <= timeout_s <= 60:
        raise ValueError("Connection timeout must be 5..60 seconds")
    profile = "codex-f-range-" + uuid.uuid4().hex
    xml = profile_xml(profile, ssid, password)
    api = Wlan()
    result = {"connected": False, "restored": False, "profile_removed": False,
              "xml_removed": False, "temporary_profile": profile}
    added, created, disconnected = False, False, False
    guid, original = None, None
    try:
        interfaces = api.interfaces()
        candidates = [item for item in interfaces if not interface_guid or
                      item["guid"].lower() == str(uuid.UUID(interface_guid.strip("{}"))).lower()]
        if len(candidates) != 1:
            raise RuntimeError("Select one explicit Wi-Fi interface; matching count=" + str(len(candidates)))
        guid = candidates[0]["guid"]
        original = api.status(guid)
        before = api.profiles(guid)
        result.update(interface=candidates[0], original=original, original_profiles=before)
        if profile in before:
            raise RuntimeError("Temporary profile collision")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            created = True
            stream.write(xml)
        # Password is inside the XML only. Keep netsh output private and return codes only.
        completed = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/netsh.exe"),
                                    "wlan", "add", "profile", "filename=" + str(path), "user=current",
                                    "interface=" + candidates[0]["alias"]],
                                   capture_output=True, timeout=20, check=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        added = profile in api.profiles(guid)
        result["profile_add_exit"] = completed.returncode
        if completed.returncode or not added:
            raise RuntimeError("Temporary current-user profile creation failed")
        api.disconnect(guid)
        disconnected = True
        ready, events = _wait(api, guid, lambda status: status["state"] == DISCONNECTED, 10)
        result["disconnect_observations"] = events
        if not ready:
            raise RuntimeError("Interface did not reach disconnected state")
        api.connect(guid, profile)
        expected_hex = ssid.encode("utf-8").hex()
        match = lambda status: (status["state"] == CONNECTED and status.get("ssid_hex") == expected_hex
                                and status["profile"] == profile
                                and (not expected_bssid or status["bssid"].lower() == expected_bssid.lower()))
        result["connected"], result["observations"] = _wait(api, guid, match, timeout_s, 2.0)
    except Exception as error:
        # Exceptions from this module contain operation codes/paths, never passwords.
        result["error"] = type(error).__name__ + ": " + str(error)
    finally:
        if guid and disconnected:
            try:
                api.disconnect(guid)
                _wait(api, guid, lambda status: status["state"] == DISCONNECTED, 10)
            except Exception as error:
                result["cleanup_disconnect_error"] = str(error)
        if guid and created:
            try:
                # netsh may have installed the profile before a timeout interrupted it.
                if profile in api.profiles(guid):
                    api.delete_profile(guid, profile)
                result["profile_removed"] = profile not in api.profiles(guid)
            except Exception as error:
                result["cleanup_profile_error"] = str(error)
        elif guid:
            result["profile_removed"] = profile not in api.profiles(guid)
        if original is not None:
            try:
                if disconnected and original["state"] == CONNECTED and original["profile"]:
                    api.connect(guid, original["profile"])
                    expected = lambda status: (status["state"] == CONNECTED and
                                              status["profile"] == original["profile"] and
                                              status.get("ssid_hex") == original.get("ssid_hex"))
                    result["restored"], result["restore_observations"] = _wait(api, guid, expected, 30, 2.0)
                else:
                    result["restored"] = api.status(guid)["state"] == original["state"]
                result["profiles_unchanged"] = api.profiles(guid) == result["original_profiles"]
            except Exception as error:
                result["restore_error"] = str(error)
        if created:
            try:
                path.unlink()
                result["xml_removed"] = not path.exists()
            except OSError:
                result["xml_cleanup_error"] = "Temporary password XML deletion failed"
        api.close()
    return result


def check_password_sequence(*, ssid, new_password, old_password, profile_path,
                            approval, interface_guid=None, expected_bssid=None, timeout_s=25.0):
    """Positive-negative-positive; report uncertainty separately from rejection."""
    if new_password == old_password:
        raise ValueError("Use distinct old and new credentials for the comparison")
    results = []
    for label, secret in (("new_before", new_password), ("old", old_password), ("new_after", new_password)):
        result = attempt_wifi_connection(ssid=ssid, password=secret, profile_path=profile_path,
                                         approval=approval, interface_guid=interface_guid,
                                         expected_bssid=expected_bssid, timeout_s=timeout_s)
        results.append({"label": label, **result})
        if result.get("error") or not all(result.get(key) for key in
                                           ("restored", "profile_removed", "xml_removed", "profiles_unchanged")):
            break
    clean = len(results) == 3 and all(not row.get("error") and
            all(row.get(key) for key in ("restored", "profile_removed", "xml_removed", "profiles_unchanged"))
            for row in results)
    confirmed = clean and results[0]["connected"] and not results[1]["connected"] and results[2]["connected"]
    return {"password_change_confirmed": bool(confirmed), "attempts": results,
            "conclusion": "positive-negative-positive association observed" if confirmed else "unconfirmed"}


def offline_self_test():
    """Synthetic XML check only: never opens WLAN API or launches netsh."""
    name, ssid, secret = "codex-offline-<>&", "试验&<SSID>", "synthetic<&>pass"
    payload = profile_xml(name, ssid, secret)
    root = ET.fromstring(payload)
    find = lambda path: root.find(path, {"w": NS}).text
    assert find("w:name") == name
    assert find("w:SSIDConfig/w:SSID/w:hex") == ssid.encode("utf-8").hex().upper()
    assert find("w:MSM/w:security/w:sharedKey/w:keyMaterial") == secret
    assert find("w:connectionMode") == "manual"
    assert find("w:MSM/w:security/w:authEncryption/w:authentication") == "WPA2PSK"
    for invalid_ssid, invalid_secret in (("", secret), ("x" * 33, secret), (ssid, "short"), (ssid, "x" * 64)):
        try:
            profile_xml(name, invalid_ssid, invalid_secret)
        except ValueError:
            continue
        raise AssertionError("Invalid XML input accepted")
    for invalid_name in ("bad\x00name", "bad\nname"):
        try:
            profile_xml(invalid_name, ssid, secret)
        except ValueError:
            continue
        raise AssertionError("Control character accepted")
    try:
        attempt_wifi_connection(ssid=ssid, password=secret,
                                profile_path=ROOT / "local-data/offline-never-created.xml", approval=False)
    except RuntimeError as error:
        assert str(error) == "Explicit approved trial required"
    else:
        raise AssertionError("Approval guard accepted false")
    structures_checked = 0
    if os.name == "nt":
        for structure, size in ((GUID, 16), (InterfaceInfo, 532), (Ssid, 36),
                                (Association, 68), (Security, 16), (Connection, 604), (ProfileInfo, 516)):
            assert C.sizeof(structure) == size, structure.__name__
            structures_checked += 1
        assert GUID.parse("00112233-4455-6677-8899-aabbccddeeff").text() == "00112233-4455-6677-8899-aabbccddeeff"
    return {"offline_xml_checks": 11, "approval_guard_checks": 1,
            "wlan_structure_layout_checks": structures_checked, "hardware_access": False}


if __name__ == "__main__":
    import json
    print(json.dumps(offline_self_test()))
