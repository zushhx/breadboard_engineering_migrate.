"""Windows WLAN metadata and restoration of an existing profile; no credential reads.

Import has no OS/network side effects. Only the approved offline runner calls connect.
"""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
import os
import uuid


class GUID(c.Structure):
    _fields_=[('a',w.DWORD),('b',w.WORD),('d',w.WORD),('tail',c.c_ubyte*8)]


class Interface(c.Structure):
    _fields_=[('guid',GUID),('description',w.WCHAR*256),('state',w.DWORD)]


class SSID(c.Structure):
    _fields_=[('length',w.DWORD),('data',c.c_ubyte*32)]


class Association(c.Structure):
    _fields_=[('ssid',SSID),('bss',w.DWORD),('bssid',c.c_ubyte*6),('phy',w.DWORD),
              ('phy_index',w.DWORD),('quality',w.DWORD),('rx',w.DWORD),('tx',w.DWORD)]


class Security(c.Structure):
    _fields_=[('enabled',w.BOOL),('one_x',w.BOOL),('auth',w.DWORD),('cipher',w.DWORD)]


class Connection(c.Structure):
    _fields_=[('state',w.DWORD),('mode',w.DWORD),('profile',w.WCHAR*256),
              ('association',Association),('security',Security)]


class Parameters(c.Structure):
    _fields_=[('mode',w.DWORD),('profile',w.LPCWSTR),('ssid',c.c_void_p),
              ('bssids',c.c_void_p),('bss_type',w.DWORD),('flags',w.DWORD)]


def validate_snapshot(snapshot):
    guid=snapshot.get('interface_guid');profile=snapshot.get('profile_name')
    if not isinstance(guid,str) or not isinstance(profile,str):
        raise ValueError('Original WLAN interface/profile missing')
    parsed=uuid.UUID(guid)
    if not profile or len(profile.encode('utf-16-le'))>510 or any(ord(ch)<32 or ord(ch)==127 for ch in profile):
        raise ValueError('Invalid existing WLAN profile name')
    return GUID.from_buffer_copy(parsed.bytes_le),profile


def api():
    if os.name!='nt':raise RuntimeError('Windows WLAN API required')
    if (c.sizeof(GUID),c.sizeof(Interface),c.sizeof(Association),c.sizeof(Connection))!=(16,532,68,604):
        raise RuntimeError('Unexpected Windows WLAN structure layout')
    dll=c.WinDLL('wlanapi.dll',use_last_error=True)
    pointer=c.c_void_p;pdword=c.POINTER(w.DWORD);ppointer=c.POINTER(pointer)
    dll.WlanOpenHandle.argtypes=[w.DWORD,pointer,pdword,c.POINTER(w.HANDLE)]
    dll.WlanEnumInterfaces.argtypes=[w.HANDLE,pointer,ppointer]
    dll.WlanQueryInterface.argtypes=[w.HANDLE,c.POINTER(GUID),w.DWORD,pointer,pdword,ppointer,pdword]
    dll.WlanConnect.argtypes=[w.HANDLE,c.POINTER(GUID),c.POINTER(Parameters),pointer]
    dll.WlanCloseHandle.argtypes=[w.HANDLE,pointer]
    dll.WlanFreeMemory.argtypes=[pointer];dll.WlanFreeMemory.restype=None
    for name in ('WlanOpenHandle','WlanEnumInterfaces','WlanQueryInterface','WlanConnect','WlanCloseHandle'):
        getattr(dll,name).restype=w.DWORD
    return dll


def check(code,action):
    if code:raise OSError(int(code),action+' failed')


@contextmanager
def handle(dll):
    negotiated=w.DWORD();opened=w.HANDLE()
    check(dll.WlanOpenHandle(2,None,c.byref(negotiated),c.byref(opened)),'WlanOpenHandle')
    try:yield opened
    finally:dll.WlanCloseHandle(opened,None)


def current_connection():
    dll=api();found=[]
    with handle(dll) as opened:
        listing=c.c_void_p()
        check(dll.WlanEnumInterfaces(opened,None,c.byref(listing)),'WlanEnumInterfaces')
        try:
            count=c.cast(listing,c.POINTER(w.DWORD))[0]
            if count>64:raise RuntimeError('Unexpected WLAN interface count')
            for index in range(count):
                interface=Interface.from_address(listing.value+8+index*c.sizeof(Interface))
                if interface.state!=1:continue
                data=c.c_void_p();size=w.DWORD();kind=w.DWORD()
                check(dll.WlanQueryInterface(opened,c.byref(interface.guid),7,None,c.byref(size),c.byref(data),c.byref(kind)),
                      'WlanQueryInterface current connection')
                try:
                    if size.value<c.sizeof(Connection):raise RuntimeError('Truncated WLAN connection metadata')
                    connection=c.cast(data,c.POINTER(Connection)).contents
                    ssid=connection.association.ssid
                    if connection.state!=1 or not 1<=ssid.length<=32:continue
                    value=dict(interface_guid=str(uuid.UUID(bytes_le=bytes(interface.guid))),
                               profile_name=connection.profile,ssid_hex=bytes(ssid.data)[:ssid.length].hex())
                    validate_snapshot(value);found.append(value)
                finally:dll.WlanFreeMemory(data)
        finally:dll.WlanFreeMemory(listing)
    if len(found)!=1:raise RuntimeError('Expected exactly one connected WLAN interface')
    return found[0]


def connect_profile(snapshot):
    guid,profile=validate_snapshot(snapshot)
    parameters=Parameters(0,profile,None,None,1,0)
    dll=api()
    with handle(dll) as opened:
        check(dll.WlanConnect(opened,c.byref(guid),c.byref(parameters),None),'WlanConnect existing profile')
    # Success only means association was requested. The caller must verify it.
