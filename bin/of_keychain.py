"""A keychain of a harness's own, kept locked but while a run of it goes (RFC 0008 §3.1; of_server, message
01a20e82-8a40): its password is random, kept by OF in a mode-600 file the run's sandbox never reads, so a run's model
that reads the keychain file reads only ciphertext; OF unlocks it just before a run and locks it as soon as the run
ends, through the Security framework, never through a command line (where ps shows it) and never with a prompt.

Never a dialog: this process forbids the framework every user interaction, so a locked keychain or a wrong password
fails instead of asking. Never a security process ended or signalled (2026-11-24: a dialog's agent force-ended on the
laptop restarted securityd, which locked the owner's login keychain and hung every keychain read on the machine).
"""
import ctypes
import ctypes.util
import fcntl
import os
import secrets
import threading

AUTO_LOCK_S = 300   # of_server's "short auto-lock": a run's holder keeps it unlocked (KEEP_S) while the run goes
KEEP_S = 60
_SETTINGS_VERS1 = 1
_UNLOCKED = 1        # kSecUnlockStateStatus


class KeychainError(Exception):
    pass


class _Settings(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("lockOnSleep", ctypes.c_ubyte), ("useLockInterval", ctypes.c_ubyte),
                ("lockInterval", ctypes.c_uint32)]


_sec = None


def _lib():
    """The Security framework, with every user interaction off for this process."""
    global _sec
    if _sec is None:
        s = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        s.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        s.SecKeychainCreate.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_ubyte,
                                        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        s.SecKeychainUnlock.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_ubyte]
        s.SecKeychainLock.argtypes = [ctypes.c_void_p]
        s.SecKeychainGetStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        s.SecKeychainSetSettings.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Settings)]
        s.SecKeychainChangePassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
                                                ctypes.c_char_p]
        s.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        for f in ("SecKeychainOpen", "SecKeychainCreate", "SecKeychainUnlock", "SecKeychainLock", "SecKeychainGetStatus",
                  "SecKeychainSetSettings", "SecKeychainChangePassword", "SecKeychainSetUserInteractionAllowed"):
            getattr(s, f).restype = ctypes.c_int32
        _check(s.SecKeychainSetUserInteractionAllowed(0), "turning prompts off")
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        s._cf = cf
        _sec = s
    return _sec


def _check(status, what):
    if status != 0:
        raise KeychainError(f"{what} failed (OSStatus {status})")


def _open(path):
    s = _lib()
    ref = ctypes.c_void_p()
    _check(s.SecKeychainOpen(os.fsencode(os.path.realpath(path)), ctypes.byref(ref)), f"opening {path}")
    return ref


def _close(ref):
    if ref:
        _lib()._cf.CFRelease(ref)


def read_password(pw_file):
    """The password from PW_FILE: a plain file of this user, mode 600, never a link."""
    try:
        fd = os.open(pw_file, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise KeychainError(f"{pw_file} cannot be read ({e.strerror})")
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise KeychainError(f"{pw_file} is not this user's alone (mode {oct(st.st_mode & 0o777)})")
        pw = os.read(fd, 4096).strip()
    finally:
        os.close(fd)
    if len(pw) < 32:
        raise KeychainError(f"{pw_file} holds no password of OF's")
    return pw


def new_password(pw_file):
    """A random password written new into PW_FILE (mode 600), returned; an existing file is refused."""
    pw = secrets.token_urlsafe(32).encode()
    fd = os.open(pw_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pw)
    return pw


def _settings(ref):
    st = _Settings(_SETTINGS_VERS1, 1, 1, AUTO_LOCK_S)   # locks on sleep and after AUTO_LOCK_S idle
    _check(_lib().SecKeychainSetSettings(ref, ctypes.byref(st)), "setting its auto-lock")


def create(path, pw_file):
    """A new keychain at PATH under a random password kept in PW_FILE, locked on sleep and after AUTO_LOCK_S."""
    pw = new_password(pw_file)
    ref = ctypes.c_void_p()
    _check(_lib().SecKeychainCreate(os.fsencode(path), len(pw), pw, 0, None, ctypes.byref(ref)), f"creating {path}")
    try:
        _settings(ref)
    finally:
        _close(ref)


def rekey(path, pw_file, old=b""):
    """Give the keychain at PATH a random password kept in PW_FILE, from OLD (the empty one `of subagent muse-login`
    gave before 2026-11-24); the owner's, in a plain terminal."""
    pw = new_password(pw_file)
    ref = _open(path)
    try:
        _check(_lib().SecKeychainUnlock(ref, len(old), old, 1), f"unlocking {path} with its old password")
        _check(_lib().SecKeychainChangePassword(ref, len(old), old, len(pw), pw), f"changing {path}'s password")
        _settings(ref)
        _lib().SecKeychainLock(ref)
    except BaseException:
        os.unlink(pw_file)   # the old password still holds
        raise
    finally:
        _close(ref)


def unlocked(path):
    """True when the keychain at PATH is unlocked now."""
    ref = _open(path)
    try:
        st = ctypes.c_uint32()
        _check(_lib().SecKeychainGetStatus(ref, ctypes.byref(st)), f"reading {path}'s state")
        return bool(st.value & _UNLOCKED)
    finally:
        _close(ref)


def opens_empty(path):
    """True when the keychain at PATH unlocks with the empty password (it is then locked again): the selftest's check
    that its password is OF's, never the empty one it had before."""
    ref = _open(path)
    try:
        if _lib().SecKeychainUnlock(ref, 0, b"", 1) != 0:
            return False
        _lib().SecKeychainLock(ref)
        return True
    finally:
        _close(ref)


def unlock(path, pw_file):
    ref = _open(path)
    try:
        pw = read_password(pw_file)
        _check(_lib().SecKeychainUnlock(ref, len(pw), pw, 1), f"unlocking {path}")
    finally:
        _close(ref)


def lock(path):
    ref = _open(path)
    try:
        _check(_lib().SecKeychainLock(ref), f"locking {path}")
    finally:
        _close(ref)


class Held:
    """`with Held(keychain, pw_file, hold_file):` the keychain unlocked for one run, kept so while it goes (again every
    KEEP_S, within AUTO_LOCK_S, so its auto-lock never comes in a run, where the harness's read would prompt), and
    locked when the last of the runs holding it ends, failures and timeouts included: each run holds HOLD_FILE shared;
    the one that gets it exclusive on its way out is the last."""

    def __init__(self, path, pw_file, hold_file):
        self.path, self.pw_file, self.hold_file = path, pw_file, hold_file
        self._stop = threading.Event()
        self._fd = None
        self._thread = None

    def __enter__(self):
        self._fd = os.open(self.hold_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_SH)
        try:
            unlock(self.path, self.pw_file)
            if not unlocked(self.path):
                raise KeychainError(f"{self.path} is still locked after its unlock")
        except BaseException:
            self._release()
            raise
        self._thread = threading.Thread(target=self._keep, daemon=True)
        self._thread.start()
        return self

    def _keep(self):
        while not self._stop.wait(KEEP_S):
            try:
                unlock(self.path, self.pw_file)
            except KeychainError:
                return

    def _release(self):
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # no other run holds it: lock the keychain
            lock(self.path)
        except BlockingIOError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._release()
        return False
